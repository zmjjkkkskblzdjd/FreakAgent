






CREATE USER 'allfusion_app'@'%' IDENTIFIED BY '__AF_MYSQL_APP_PASSWORD__';

GRANT SELECT, INSERT, UPDATE, DELETE ON allfusion.* TO 'allfusion_app'@'%';
