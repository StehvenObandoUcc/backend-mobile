-- Fase 4a: vincula cada usuario con Supabase Auth (verificación de correo y recuperación de contraseña).
-- Cómo correrla: Supabase > SQL Editor > New query > pega este archivo > Run.
-- Es idempotente: se puede ejecutar varias veces sin error.
-- Correrla ANTES de activar AUTH_PROVIDER=supabase en el backend.

ALTER TABLE public.users ADD COLUMN IF NOT EXISTS auth_id text; -- id del usuario en auth.users (Supabase Auth)
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_auth_id ON public.users (auth_id) WHERE auth_id IS NOT NULL;
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS email_verified boolean NOT NULL DEFAULT false;

-- Verificación: deben aparecer las dos columnas (auth_id, email_verified) y el índice idx_users_auth_id.
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = 'users'
  AND column_name IN ('auth_id', 'email_verified');

SELECT indexname FROM pg_indexes
WHERE schemaname = 'public' AND tablename = 'users' AND indexname = 'idx_users_auth_id';
