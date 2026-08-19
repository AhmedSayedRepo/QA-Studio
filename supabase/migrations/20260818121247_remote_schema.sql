


SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;


COMMENT ON SCHEMA "public" IS 'standard public schema';



CREATE EXTENSION IF NOT EXISTS "pg_net" WITH SCHEMA "public";






CREATE EXTENSION IF NOT EXISTS "hypopg" WITH SCHEMA "extensions";






CREATE EXTENSION IF NOT EXISTS "index_advisor" WITH SCHEMA "extensions";






CREATE EXTENSION IF NOT EXISTS "pg_stat_statements" WITH SCHEMA "extensions";






CREATE EXTENSION IF NOT EXISTS "pgcrypto" WITH SCHEMA "extensions";






CREATE EXTENSION IF NOT EXISTS "supabase_vault" WITH SCHEMA "vault";






CREATE EXTENSION IF NOT EXISTS "uuid-ossp" WITH SCHEMA "extensions";






CREATE OR REPLACE FUNCTION "public"."dispatch_remote_run"() RETURNS "trigger"
    LANGUAGE "plpgsql" SECURITY DEFINER
    SET "search_path" TO 'public', 'vault', 'net'
    AS $$
declare
  v_pat text;
begin
  select ds.decrypted_secret into v_pat
    from vault.decrypted_secrets ds
   where ds.name = 'github_dispatch_pat'
   order by ds.created_at desc
   limit 1;
  if v_pat is null or length(trim(v_pat)) = 0 then
    update public.remote_runs
       set summary = 'not auto-dispatched: github_dispatch_pat missing in Vault — dispatch manually'
     where id = new.id;
    return new;
  end if;
  perform net.http_post(
    url := 'https://api.github.com/repos/AhmedSayedRepo/QA-Studio/actions/workflows/remote-run.yml/dispatches',
    headers := jsonb_build_object(
      'Authorization', 'Bearer ' || v_pat,
      'Accept', 'application/vnd.github+json',
      'X-GitHub-Api-Version', '2022-11-28',
      'Content-Type', 'application/json',
      'User-Agent', 'qa-studio-dispatch'),
    body := jsonb_build_object('ref', 'main',
                               'inputs', jsonb_build_object('run_id', new.id::text)));
  return new;
exception when others then
  -- pg_net enqueue failures must never abort the enqueue itself.
  begin
    update public.remote_runs
       set summary = 'auto-dispatch error: ' || left(sqlerrm, 160)
     where id = new.id;
  exception when others then null;
  end;
  return new;
end $$;


ALTER FUNCTION "public"."dispatch_remote_run"() OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."get_my_credentials_status"() RETURNS TABLE("azure_org" "text", "ai_provider" "text", "ai_model" "text", "has_pat" boolean, "has_key" boolean, "gmail_sender" "text", "has_gmail" boolean, "updated_at" timestamp with time zone)
    LANGUAGE "sql" SECURITY DEFINER
    SET "search_path" TO 'public'
    AS $$
  select c.azure_org, c.ai_provider, c.ai_model,
         c.azure_pat_secret_id is not null,
         c.ai_key_secret_id is not null,
         c.gmail_sender,
         c.gmail_app_pass_secret_id is not null,
         c.updated_at
  from public.user_credentials c
  where c.user_id = auth.uid();
$$;


ALTER FUNCTION "public"."get_my_credentials_status"() OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."set_my_credentials"("p_azure_org" "text" DEFAULT NULL::"text", "p_azure_pat" "text" DEFAULT NULL::"text", "p_ai_provider" "text" DEFAULT NULL::"text", "p_ai_api_key" "text" DEFAULT NULL::"text", "p_ai_model" "text" DEFAULT NULL::"text", "p_gmail_sender" "text" DEFAULT NULL::"text", "p_gmail_sender_name" "text" DEFAULT NULL::"text", "p_gmail_app_pass" "text" DEFAULT NULL::"text") RETURNS "void"
    LANGUAGE "plpgsql" SECURITY DEFINER
    SET "search_path" TO 'public'
    AS $$
declare
  v_uid uuid := auth.uid();
  v_pat_sid uuid;
  v_key_sid uuid;
  v_gmail_sid uuid;
begin
  if v_uid is null then
    raise exception 'not authenticated';
  end if;
  insert into public.user_credentials (user_id) values (v_uid)
  on conflict (user_id) do nothing;

  update public.user_credentials set
    azure_org         = coalesce(nullif(trim(p_azure_org), ''), azure_org),
    ai_provider       = coalesce(nullif(trim(p_ai_provider), ''), ai_provider),
    ai_model          = coalesce(p_ai_model, ai_model),
    gmail_sender      = coalesce(nullif(trim(p_gmail_sender), ''), gmail_sender),
    gmail_sender_name = coalesce(p_gmail_sender_name, gmail_sender_name),
    updated_at        = now()
  where user_id = v_uid;

  if p_azure_pat is not null and length(trim(p_azure_pat)) > 0 then
    select azure_pat_secret_id into v_pat_sid from public.user_credentials where user_id = v_uid;
    if v_pat_sid is null then
      v_pat_sid := vault.create_secret(trim(p_azure_pat), 'azure_pat:' || v_uid::text);
      update public.user_credentials set azure_pat_secret_id = v_pat_sid where user_id = v_uid;
    else
      perform vault.update_secret(v_pat_sid, trim(p_azure_pat));
    end if;
  end if;

  if p_ai_api_key is not null and length(trim(p_ai_api_key)) > 0 then
    select ai_key_secret_id into v_key_sid from public.user_credentials where user_id = v_uid;
    if v_key_sid is null then
      v_key_sid := vault.create_secret(trim(p_ai_api_key), 'ai_key:' || v_uid::text);
      update public.user_credentials set ai_key_secret_id = v_key_sid where user_id = v_uid;
    else
      perform vault.update_secret(v_key_sid, trim(p_ai_api_key));
    end if;
  end if;

  if p_gmail_app_pass is not null and length(trim(p_gmail_app_pass)) > 0 then
    select gmail_app_pass_secret_id into v_gmail_sid from public.user_credentials where user_id = v_uid;
    if v_gmail_sid is null then
      v_gmail_sid := vault.create_secret(trim(p_gmail_app_pass), 'gmail_app_pass:' || v_uid::text);
      update public.user_credentials set gmail_app_pass_secret_id = v_gmail_sid where user_id = v_uid;
    else
      perform vault.update_secret(v_gmail_sid, trim(p_gmail_app_pass));
    end if;
  end if;
end $$;


ALTER FUNCTION "public"."set_my_credentials"("p_azure_org" "text", "p_azure_pat" "text", "p_ai_provider" "text", "p_ai_api_key" "text", "p_ai_model" "text", "p_gmail_sender" "text", "p_gmail_sender_name" "text", "p_gmail_app_pass" "text") OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."worker_get_credentials"("p_user_id" "uuid") RETURNS TABLE("azure_org" "text", "azure_pat" "text", "ai_provider" "text", "ai_api_key" "text", "ai_model" "text", "gmail_sender" "text", "gmail_sender_name" "text", "gmail_app_pass" "text")
    LANGUAGE "plpgsql" SECURITY DEFINER
    SET "search_path" TO 'public', 'vault'
    AS $$
begin
  update public.user_credentials set last_used_at = now() where user_id = p_user_id;
  return query
    select c.azure_org,
           coalesce((select ds.decrypted_secret from vault.decrypted_secrets ds where ds.id = c.azure_pat_secret_id), ''),
           c.ai_provider,
           coalesce((select ds.decrypted_secret from vault.decrypted_secrets ds where ds.id = c.ai_key_secret_id), ''),
           c.ai_model,
           c.gmail_sender,
           c.gmail_sender_name,
           coalesce((select ds.decrypted_secret from vault.decrypted_secrets ds where ds.id = c.gmail_app_pass_secret_id), '')
    from public.user_credentials c
    where c.user_id = p_user_id;
end $$;


ALTER FUNCTION "public"."worker_get_credentials"("p_user_id" "uuid") OWNER TO "postgres";

SET default_tablespace = '';

SET default_table_access_method = "heap";


CREATE TABLE IF NOT EXISTS "public"."ai_usage_events" (
    "id" bigint NOT NULL,
    "user_id" "uuid" NOT NULL,
    "user_email" "text" NOT NULL,
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "provider" "text" NOT NULL,
    "model" "text" NOT NULL,
    "input_tokens" integer DEFAULT 0 NOT NULL,
    "output_tokens" integer DEFAULT 0 NOT NULL,
    "tag" "text"
);


ALTER TABLE "public"."ai_usage_events" OWNER TO "postgres";


ALTER TABLE "public"."ai_usage_events" ALTER COLUMN "id" ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME "public"."ai_usage_events_id_seq"
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);



CREATE TABLE IF NOT EXISTS "public"."org_settings" (
    "key" "text" NOT NULL,
    "value" "jsonb" DEFAULT '{}'::"jsonb" NOT NULL,
    "updated_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "updated_by" "uuid"
);


ALTER TABLE "public"."org_settings" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."orgs" (
    "id" "text" NOT NULL,
    "name" "text" NOT NULL,
    "contact_name" "text",
    "contact_email" "text",
    "contact_phone" "text",
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "updated_at" timestamp with time zone DEFAULT "now"() NOT NULL
);


ALTER TABLE "public"."orgs" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."remote_run_events" (
    "id" bigint NOT NULL,
    "run_id" "uuid" NOT NULL,
    "seq" integer NOT NULL,
    "kind" "text" NOT NULL,
    "payload" "jsonb" DEFAULT '{}'::"jsonb" NOT NULL,
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL
);


ALTER TABLE "public"."remote_run_events" OWNER TO "postgres";


ALTER TABLE "public"."remote_run_events" ALTER COLUMN "id" ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME "public"."remote_run_events_id_seq"
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);



CREATE TABLE IF NOT EXISTS "public"."remote_runs" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "kind" "text" NOT NULL,
    "project" "text" NOT NULL,
    "plan_id" bigint NOT NULL,
    "story_ids" "jsonb" DEFAULT '[]'::"jsonb" NOT NULL,
    "existing_mode" "text" DEFAULT 'skip'::"text" NOT NULL,
    "output_lang" "text" DEFAULT 'ar'::"text" NOT NULL,
    "status" "text" DEFAULT 'queued'::"text" NOT NULL,
    "control" "text",
    "summary" "text",
    "created_by" "text",
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "started_at" timestamp with time zone,
    "finished_at" timestamp with time zone,
    "email_recipients" "jsonb" DEFAULT '[]'::"jsonb" NOT NULL,
    CONSTRAINT "remote_runs_control_check" CHECK (("control" = ANY (ARRAY['pause'::"text", 'resume'::"text", 'stop'::"text"]))),
    CONSTRAINT "remote_runs_existing_mode_check" CHECK (("existing_mode" = ANY (ARRAY['skip'::"text", 'evaluate'::"text"]))),
    CONSTRAINT "remote_runs_kind_check" CHECK (("kind" = ANY (ARRAY['titles'::"text", 'steps'::"text"]))),
    CONSTRAINT "remote_runs_status_check" CHECK (("status" = ANY (ARRAY['queued'::"text", 'running'::"text", 'paused'::"text", 'done'::"text", 'stopped'::"text", 'error'::"text"])))
);


ALTER TABLE "public"."remote_runs" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."user_credentials" (
    "user_id" "uuid" NOT NULL,
    "azure_org" "text" DEFAULT ''::"text" NOT NULL,
    "ai_provider" "text" DEFAULT 'anthropic'::"text" NOT NULL,
    "ai_model" "text" DEFAULT ''::"text" NOT NULL,
    "azure_pat_secret_id" "uuid",
    "ai_key_secret_id" "uuid",
    "updated_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "last_used_at" timestamp with time zone,
    "gmail_sender" "text",
    "gmail_sender_name" "text",
    "gmail_app_pass_secret_id" "uuid"
);


ALTER TABLE "public"."user_credentials" OWNER TO "postgres";


ALTER TABLE ONLY "public"."ai_usage_events"
    ADD CONSTRAINT "ai_usage_events_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."org_settings"
    ADD CONSTRAINT "org_settings_pkey" PRIMARY KEY ("key");



ALTER TABLE ONLY "public"."orgs"
    ADD CONSTRAINT "orgs_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."remote_run_events"
    ADD CONSTRAINT "remote_run_events_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."remote_run_events"
    ADD CONSTRAINT "remote_run_events_run_id_seq_key" UNIQUE ("run_id", "seq");



ALTER TABLE ONLY "public"."remote_runs"
    ADD CONSTRAINT "remote_runs_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."user_credentials"
    ADD CONSTRAINT "user_credentials_pkey" PRIMARY KEY ("user_id");



CREATE INDEX "ai_usage_events_created_at_idx" ON "public"."ai_usage_events" USING "btree" ("created_at");



CREATE INDEX "ai_usage_events_user_idx" ON "public"."ai_usage_events" USING "btree" ("user_id", "created_at");



CREATE INDEX "remote_run_events_run_seq" ON "public"."remote_run_events" USING "btree" ("run_id", "seq");



CREATE INDEX "remote_runs_status" ON "public"."remote_runs" USING "btree" ("status");



CREATE OR REPLACE TRIGGER "remote_runs_auto_dispatch" AFTER INSERT ON "public"."remote_runs" FOR EACH ROW EXECUTE FUNCTION "public"."dispatch_remote_run"();



ALTER TABLE ONLY "public"."ai_usage_events"
    ADD CONSTRAINT "ai_usage_events_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "auth"."users"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."org_settings"
    ADD CONSTRAINT "org_settings_updated_by_fkey" FOREIGN KEY ("updated_by") REFERENCES "auth"."users"("id");



ALTER TABLE ONLY "public"."remote_run_events"
    ADD CONSTRAINT "remote_run_events_run_id_fkey" FOREIGN KEY ("run_id") REFERENCES "public"."remote_runs"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."user_credentials"
    ADD CONSTRAINT "user_credentials_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "auth"."users"("id") ON DELETE CASCADE;



ALTER TABLE "public"."ai_usage_events" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."org_settings" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."orgs" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."remote_run_events" ENABLE ROW LEVEL SECURITY;


CREATE POLICY "remote_run_events_select" ON "public"."remote_run_events" FOR SELECT TO "authenticated" USING ((EXISTS ( SELECT 1
   FROM "public"."remote_runs" "r"
  WHERE (("r"."id" = "remote_run_events"."run_id") AND ("r"."created_by" = (( SELECT "auth"."uid"() AS "uid"))::"text")))));



ALTER TABLE "public"."remote_runs" ENABLE ROW LEVEL SECURITY;


CREATE POLICY "remote_runs_insert" ON "public"."remote_runs" FOR INSERT TO "authenticated" WITH CHECK (("created_by" = (( SELECT "auth"."uid"() AS "uid"))::"text"));



CREATE POLICY "remote_runs_select" ON "public"."remote_runs" FOR SELECT TO "authenticated" USING (("created_by" = (( SELECT "auth"."uid"() AS "uid"))::"text"));



CREATE POLICY "remote_runs_update_control" ON "public"."remote_runs" FOR UPDATE TO "authenticated" USING (("created_by" = (( SELECT "auth"."uid"() AS "uid"))::"text")) WITH CHECK (("created_by" = (( SELECT "auth"."uid"() AS "uid"))::"text"));



ALTER TABLE "public"."user_credentials" ENABLE ROW LEVEL SECURITY;




ALTER PUBLICATION "supabase_realtime" OWNER TO "postgres";


ALTER PUBLICATION "supabase_realtime" ADD TABLE ONLY "public"."remote_run_events";



ALTER PUBLICATION "supabase_realtime" ADD TABLE ONLY "public"."remote_runs";



GRANT USAGE ON SCHEMA "public" TO "postgres";
GRANT USAGE ON SCHEMA "public" TO "anon";
GRANT USAGE ON SCHEMA "public" TO "authenticated";
GRANT USAGE ON SCHEMA "public" TO "service_role";





























































































































































































REVOKE ALL ON FUNCTION "public"."dispatch_remote_run"() FROM PUBLIC;
GRANT ALL ON FUNCTION "public"."dispatch_remote_run"() TO "service_role";



REVOKE ALL ON FUNCTION "public"."get_my_credentials_status"() FROM PUBLIC;
GRANT ALL ON FUNCTION "public"."get_my_credentials_status"() TO "authenticated";
GRANT ALL ON FUNCTION "public"."get_my_credentials_status"() TO "service_role";



REVOKE ALL ON FUNCTION "public"."set_my_credentials"("p_azure_org" "text", "p_azure_pat" "text", "p_ai_provider" "text", "p_ai_api_key" "text", "p_ai_model" "text", "p_gmail_sender" "text", "p_gmail_sender_name" "text", "p_gmail_app_pass" "text") FROM PUBLIC;
GRANT ALL ON FUNCTION "public"."set_my_credentials"("p_azure_org" "text", "p_azure_pat" "text", "p_ai_provider" "text", "p_ai_api_key" "text", "p_ai_model" "text", "p_gmail_sender" "text", "p_gmail_sender_name" "text", "p_gmail_app_pass" "text") TO "authenticated";
GRANT ALL ON FUNCTION "public"."set_my_credentials"("p_azure_org" "text", "p_azure_pat" "text", "p_ai_provider" "text", "p_ai_api_key" "text", "p_ai_model" "text", "p_gmail_sender" "text", "p_gmail_sender_name" "text", "p_gmail_app_pass" "text") TO "service_role";



REVOKE ALL ON FUNCTION "public"."worker_get_credentials"("p_user_id" "uuid") FROM PUBLIC;
GRANT ALL ON FUNCTION "public"."worker_get_credentials"("p_user_id" "uuid") TO "service_role";
























GRANT ALL ON TABLE "public"."ai_usage_events" TO "anon";
GRANT ALL ON TABLE "public"."ai_usage_events" TO "authenticated";
GRANT ALL ON TABLE "public"."ai_usage_events" TO "service_role";



GRANT ALL ON SEQUENCE "public"."ai_usage_events_id_seq" TO "anon";
GRANT ALL ON SEQUENCE "public"."ai_usage_events_id_seq" TO "authenticated";
GRANT ALL ON SEQUENCE "public"."ai_usage_events_id_seq" TO "service_role";



GRANT ALL ON TABLE "public"."org_settings" TO "service_role";



GRANT ALL ON TABLE "public"."orgs" TO "anon";
GRANT ALL ON TABLE "public"."orgs" TO "authenticated";
GRANT ALL ON TABLE "public"."orgs" TO "service_role";



GRANT ALL ON TABLE "public"."remote_run_events" TO "anon";
GRANT ALL ON TABLE "public"."remote_run_events" TO "authenticated";
GRANT ALL ON TABLE "public"."remote_run_events" TO "service_role";



GRANT ALL ON SEQUENCE "public"."remote_run_events_id_seq" TO "anon";
GRANT ALL ON SEQUENCE "public"."remote_run_events_id_seq" TO "authenticated";
GRANT ALL ON SEQUENCE "public"."remote_run_events_id_seq" TO "service_role";



GRANT ALL ON TABLE "public"."remote_runs" TO "anon";
GRANT ALL ON TABLE "public"."remote_runs" TO "authenticated";
GRANT ALL ON TABLE "public"."remote_runs" TO "service_role";



GRANT ALL ON TABLE "public"."user_credentials" TO "service_role";









ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON SEQUENCES TO "postgres";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON SEQUENCES TO "anon";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON SEQUENCES TO "authenticated";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON SEQUENCES TO "service_role";






ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON FUNCTIONS TO "postgres";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON FUNCTIONS TO "anon";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON FUNCTIONS TO "authenticated";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON FUNCTIONS TO "service_role";






ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON TABLES TO "postgres";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON TABLES TO "anon";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON TABLES TO "authenticated";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON TABLES TO "service_role";































