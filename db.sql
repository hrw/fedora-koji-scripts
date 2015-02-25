CREATE TABLE nvrs(
	build_id       integer(24),
	package_name   varchar(64),
	nvr            varchar(128),
	arch           varchar(8),
	tag            varchar(8),
	owner_name     varchar(32),
	task_id        integer(24),
	creation_time  integer,
	epoch          integer,
	version        varchar,
	release        varchar,
	state          integer
);
