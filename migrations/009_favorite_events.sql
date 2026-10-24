-- Record favorite changes without storing prompt contents. Existing events remain intact.
BEGIN;
ALTER TABLE shoe_workbench.events DROP CONSTRAINT IF EXISTS events_event_type_check;
ALTER TABLE shoe_workbench.events ADD CONSTRAINT events_event_type_check
  CHECK (event_type IN ('template_open','prompt_copy','version_select','favorite_add','favorite_remove'));
COMMIT;
