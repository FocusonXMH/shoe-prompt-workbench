BEGIN;
ALTER TABLE shoe_workbench.template_versions ADD COLUMN IF NOT EXISTS display_name text;
ALTER TABLE shoe_workbench.template_versions ADD COLUMN IF NOT EXISTS revision integer NOT NULL DEFAULT 1;
ALTER TABLE shoe_workbench.template_versions ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE shoe_workbench.events ADD COLUMN IF NOT EXISTS stats_excluded boolean NOT NULL DEFAULT false;
ALTER TABLE shoe_workbench.field_usage ADD COLUMN IF NOT EXISTS stats_excluded boolean NOT NULL DEFAULT false;
CREATE TABLE IF NOT EXISTS shoe_workbench.version_revisions (
 template_id text NOT NULL, version integer NOT NULL, revision integer NOT NULL,
 content jsonb NOT NULL, changed_by text, changed_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(template_id,version,revision),
 FOREIGN KEY(template_id,version) REFERENCES shoe_workbench.template_versions(template_id,version) ON DELETE CASCADE
);
GRANT SELECT,INSERT,UPDATE,DELETE ON shoe_workbench.version_revisions TO shoe_workbench_staging_app;
COMMIT;
