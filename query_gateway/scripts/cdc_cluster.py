# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request
import warnings

warnings.filterwarnings("ignore", module="paramiko")

try:
    import paramiko
except ImportError:  # pragma: no cover
    print("缺少 paramiko：python -m pip install paramiko")
    raise SystemExit(2)

HOST = os.getenv("CDC_HOST", "127.0.0.1")
USER = os.getenv("CDC_SSH_USER", "root")
PASSWORD = os.getenv("CDC_SSH_PASSWORD", "")
JDK_HOME = os.getenv("CDC_JDK_HOME", "/usr/lib/jvm/java-1.8.0-openjdk.x86_64")   
KAFKA_HOME = os.getenv("CDC_KAFKA_HOME", "/opt/kafka")
CONNECTOR = os.getenv("CDC_CONNECTOR", "salesbiz-mysql-source")

ZK_PORT, KAFKA_PORT, CONNECT_PORT = 2181, 9092, 8083

PROFILES = {
    "2.5g": {"zk": "256M", "kafka": "768M", "connect": "512M"},
    "3g": {"zk": None, "kafka": None, "connect": "1G"},
}

COMPONENTS = [
    ("ZK", ZK_PORT, "bin/zookeeper-server-start.sh", "config/zookeeper.properties",
     "/opt/kafka/logs/zookeeper.out", "zk"),
    ("Kafka", KAFKA_PORT, "bin/kafka-server-start.sh", "config/server.properties",
     "/opt/kafka/logs/kafkaServer.out", "kafka"),
    ("Connect", CONNECT_PORT, "bin/connect-distributed.sh", "config/connect-distributed.properties",
     "/opt/kafka/logs/connectDistributed.out", "connect"),
]


def _utf8_stdout() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


def port_open(port: int, host: str = HOST, timeout: float = 1.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


class Remote:

    def __init__(self) -> None:
        self.client = paramiko.SSHClient()
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    def connect(self) -> None:
        self.client.connect(HOST, username=USER, password=PASSWORD, timeout=15)

    def run(self, cmd: str, timeout: int = 60) -> tuple[int, str, str]:
        _, stdout, stderr = self.client.exec_command(cmd, timeout=timeout)
        out = stdout.read().decode("utf-8", errors="replace").strip()
        err = stderr.read().decode("utf-8", errors="replace").strip()
        return stdout.channel.recv_exit_status(), out, err

    def close(self) -> None:
        self.client.close()


VM_HOSTS = [
    ("hadoop01", "192.168.1.134"),
    ("hadoop02", HOST),
    ("hadoop03", "192.168.1.136"),
]


def ssh_ready(ip: str, timeout: float = 5.0) -> bool:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(ip, username=USER, password=PASSWORD, timeout=timeout,
                       banner_timeout=timeout, auth_timeout=timeout,
                       allow_agent=False, look_for_keys=False)
        _, stdout, _ = client.exec_command("true", timeout=timeout)
        return stdout.channel.recv_exit_status() == 0
    except Exception:
        return False
    finally:
        client.close()


def cmd_probe(wait_sec: int, hosts_arg: str = "") -> int:
    hosts = [tuple(h.split("=", 1)) for h in hosts_arg.split(",") if "=" in h] or VM_HOSTS
    print(f"===== 等待 VM SSH 可登录（最多 {wait_sec}s）=====")
    deadline = time.time() + wait_sec
    pending = [name for name, _ in hosts]
    last_report = 0.0
    while True:
        for name, ip in list(hosts):
            if name not in pending:
                continue
            if ssh_ready(ip):
                pending.remove(name)
                print(f"  {name}({ip}) SSH 可登录")
        if not pending:
            print("===== 全部 VM SSH 可登录 =====")
            return 0
        if time.time() >= deadline:
            print(f"  超时：{', '.join(pending)} 仍不可登录")
            return 1
        now = time.time()
        if now - last_report >= 30:
            print(f"  仍在等待：{', '.join(pending)}（剩余 {int(deadline - now)}s）")
            last_report = now
        time.sleep(5)


def start_cmd(script: str, conf: str, heap: str | None) -> str:
    env = f"JAVA_HOME={JDK_HOME}"
    if heap:
        env += f" KAFKA_HEAP_OPTS='-Xmx{heap} -Xms256M'"
    return f"cd {KAFKA_HOME} && {env} {script} -daemon {conf}"


def wait_port(port: int, timeout: int, label: str) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if port_open(port):
            return True
        time.sleep(1)
    print(f"      超时：{label} :{port} 在 {timeout}s 内未监听")
    return False


STALE_BINLOG_HINTS = ("no longer available on the server", "Could not find first log file")

RESNAPSHOT_HINT = f"""\
      诊断：connector 记录的 binlog 位点已被 MySQL 清理，task 无法自愈。
      处置（实测有效顺序；只做 NOTES 的前三步会失败——旧位点还在 Kafka 的 connect-offsets 里）：
        ① curl -X DELETE http://{HOST}:{CONNECT_PORT}/connectors/{CONNECTOR}
        ② 停 Connect 进程，删 Kafka 内部 topic connect-offsets（Connect 重启会自动重建），再起 Connect
        ③ CH 四张物理表 TRUNCATE + 四张 kafka_* 表 DETACH/ATTACH（重置 consumer offset）
        ④ 重新 POST 注册 connector（snapshot.mode=initial，拉 MySQL 当前全量）
      代价：中断期间的版本历史不可恢复；最新态对账后一致。"""


def connector_status() -> dict | None:
    url = f"http://{HOST}:{CONNECT_PORT}/connectors/{CONNECTOR}/status"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return None


def split_status(data: dict) -> tuple[str, list[str], str]:
    conn = (data.get("connector") or {}).get("state", "UNKNOWN")
    tasks = [t.get("state", "UNKNOWN") for t in data.get("tasks") or []]
    trace = next((t["trace"] for t in data.get("tasks") or [] if t.get("trace")), "")
    return conn, tasks, trace


def wait_connector(timeout: int) -> str:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        data = connector_status()
        if data:
            conn, tasks, trace = split_status(data)
            last = (conn, tasks)
            if conn == "RUNNING" and tasks and all(t == "RUNNING" for t in tasks):
                return "ok"
            if "FAILED" in tasks and any(h in trace for h in STALE_BINLOG_HINTS):
                print(RESNAPSHOT_HINT)
                return "stale"
        time.sleep(2)
    if last:
        print(f"      超时：connector={last[0]} tasks={last[1]}")
    else:
        print(f"      超时：未取到 {CONNECTOR} 的状态（可能未注册到 Connect）")
    return "timeout"


def dump_log(remote: Remote, logfile: str) -> None:
    rc, out, _ = remote.run(
        f"grep -A 20 'ERROR' {logfile} 2>/dev/null | tail -n 40", timeout=20)
    if not out:
        _, out, _ = remote.run(f"tail -n 20 {logfile} 2>/dev/null", timeout=20)
    if out:
        print(f"      --- {logfile}（ERROR 上下文）---")
        for line in out.splitlines():
            print(f"      {line}")
    elif rc != 0:
        print(f"      （{logfile} 不存在，启动可能未落地）")


def cmd_status() -> int:
    print(f"===== hadoop02 CDC 状态（{HOST}） =====")
    if not port_open(22):
        print("  VM 不可达：22 端口连不上，请先在 VMware 里启动 hadoop02。")
        return 1
    states = []
    for name, port, *_ in COMPONENTS:
        ok = port_open(port)
        states.append(ok)
        print(f"  {name:<8}:{port}  {'UP' if ok else 'DOWN'}")
    data = connector_status()
    if data:
        conn, tasks, trace = split_status(data)
        print(f"  connector {CONNECTOR}: {conn}（tasks: {', '.join(tasks) or '无'}）")
        if trace:
            first = trace.splitlines()[0]
            print(f"    异常：{first[:160]}{'…' if len(first) > 160 else ''}")
            if any(h in trace for h in STALE_BINLOG_HINTS):
                print("    位点已失效：需按 NOTES.md「CDC 中断恢复 > 7 天」重快照")
        healthy = bool(all(states) and conn == "RUNNING" and tasks
                       and all(t == "RUNNING" for t in tasks))
    else:
        print(f"  connector {CONNECTOR}: 未取到状态（Connect 未起或 connector 未注册）")
        healthy = False
    return 0 if healthy else 1


def cmd_start(profile: str, timeouts: dict[str, int]) -> int:
    heap = PROFILES[profile]
    print(f"===== hadoop02 CDC 集群启动（{HOST}，堆档 {profile}） =====")
    print(f"  ZK={heap['zk'] or '默认'}  Kafka={heap['kafka'] or '默认'}  Connect={heap['connect']}")

    if not port_open(22):
        print("  VM 不可达：22 端口连不上，请先在 VMware 里启动 hadoop02。")
        return 1

    remote = Remote()
    try:
        remote.connect()
    except Exception as exc:
        print(f"  SSH 连接失败：{exc}")
        return 1

    try:
        total = len(COMPONENTS)
        for idx, (name, port, script, conf, logfile, key) in enumerate(COMPONENTS, 1):
            head = f"  [{idx}/{total}] {name:<8}:{port}"
            if port_open(port):
                print(f"{head}  已在运行，跳过")
                continue

            rc, out, err = remote.run(start_cmd(script, conf, heap[key]), timeout=60)
            if rc != 0:
                print(f"{head}  启动失败（exit={rc}）")
                if err:
                    print(f"      {err}")
                dump_log(remote, logfile)
                return 1
            print(f"{head}  已下发启动，等待就绪 ...")
            if not wait_port(port, timeouts[key], name):
                dump_log(remote, logfile)
                return 1
            print(f"{head}  OK")

        print(f"  [收口] 校验 connector {CONNECTOR} ...")
        result = wait_connector(timeouts["connector"])
        if result != "ok":
            if result != "stale":            
                dump_log(remote, "/opt/kafka/logs/connectDistributed.out")
            return 1
        print(f"  [收口] connector {CONNECTOR} 与全部 task 均 RUNNING")
    finally:
        remote.close()

    print("===== CDC 管道就绪：MySQL(134) → Debezium → Kafka → ClickHouse(136) =====")
    return 0


def main() -> int:
    _utf8_stdout()
    parser = argparse.ArgumentParser(description="hadoop02 CDC 集群启动/状态")
    sub = parser.add_subparsers(dest="action", required=True)

    sub.add_parser("status", help="只查端口与 connector 状态，不做任何启动")

    p_probe = sub.add_parser("probe", help="等 VM 到达 SSH 可登录状态（真实登录，不只看端口）")
    p_probe.add_argument("--wait", type=int, default=480, help="最长等待秒（默认 480）")
    p_probe.add_argument("--hosts", default="",
                         help='逗号分隔 name=ip，如 "hadoop01=192.168.1.134,..."')

    p_start = sub.add_parser("start", help="幂等启动 ZK/Kafka/Connect 并校验 connector")
    p_start.add_argument("--profile", choices=sorted(PROFILES), default="2.5g",
                         help="堆档位（默认 2.5g，当前 VM 内存 ~2.3G）")
    p_start.add_argument("--timeout-zk", type=int, default=60, help="ZK 就绪超时秒（默认 60）")
    p_start.add_argument("--timeout-kafka", type=int, default=90, help="Kafka 就绪超时秒（默认 90）")
    p_start.add_argument("--timeout-connect", type=int, default=90, help="Connect 就绪超时秒（默认 90）")
    p_start.add_argument("--timeout-connector", type=int, default=120,
                         help="connector 恢复到 RUNNING 的超时秒（默认 120）")

    args = parser.parse_args()
    if args.action == "status":
        return cmd_status()
    if args.action == "probe":
        return cmd_probe(args.wait, args.hosts)
    return cmd_start(args.profile, {
        "zk": args.timeout_zk,
        "kafka": args.timeout_kafka,
        "connect": args.timeout_connect,
        "connector": args.timeout_connector,
    })


if __name__ == "__main__":
    raise SystemExit(main())
