-- Runs once when the postgres container's data volume is first created.
-- Provisions a separate database for the automated test suite so pytest never
-- touches the "chatfolio" database backing a developer's running api/worker containers.
CREATE DATABASE chatfolio_test;
