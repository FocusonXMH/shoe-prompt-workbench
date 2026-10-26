BEGIN;
CREATE TABLE IF NOT EXISTS shoe_workbench.help_articles (
 id text PRIMARY KEY, title text NOT NULL, content text NOT NULL DEFAULT '', images jsonb NOT NULL DEFAULT '[]',
 status text NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','published','withdrawn')),
 created_by text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS shoe_workbench.help_reads (
 article_id text REFERENCES shoe_workbench.help_articles(id) ON DELETE CASCADE, user_id text NOT NULL,
 read_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(article_id,user_id));
CREATE TABLE IF NOT EXISTS shoe_workbench.help_onboarding (
 user_id text PRIMARY KEY, seen_at timestamptz NOT NULL DEFAULT now());
ALTER TABLE shoe_workbench.announcements ADD COLUMN IF NOT EXISTS help_article_id text;
ALTER TABLE shoe_workbench.feedback ADD COLUMN IF NOT EXISTS template_reference jsonb;
INSERT INTO shoe_workbench.help_articles(id,title,content,images,status,created_by)
 SELECT 'getting-started','工作台使用入门',COALESCE(value->>'text',''),COALESCE(value->'images','[]'::jsonb),'published','migration'
 FROM shoe_workbench.settings WHERE key='help' ON CONFLICT(id) DO NOTHING;
INSERT INTO shoe_workbench.help_articles(id,title,content,status,created_by)
 VALUES('getting-started','工作台使用入门','1. 选择模板并填写产品、场景等信息。\n2. 生成并复制完整提示词，发送给具备相应能力的 AI。\n3. 如有问题可在需求反馈中说明，并附上参考素材或关联模板。','published','migration') ON CONFLICT(id) DO NOTHING;
GRANT SELECT,INSERT,UPDATE,DELETE ON shoe_workbench.help_articles,shoe_workbench.help_reads,shoe_workbench.help_onboarding TO shoe_workbench_staging_app;
COMMIT;
