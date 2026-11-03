-- Renovación de sesión: permite invalidar las sesiones abiertas cuando la persona cambia su contraseña.
-- Cómo correrla: Supabase > SQL Editor > New query > pega este archivo > Run.
-- Es idempotente: se puede ejecutar varias veces sin error.
-- Correrla ANTES de publicar el backend con POST /api/v1/auth/refresh.
-- Valor: segundos Unix. Los tokens emitidos antes de ese momento (claim `iat`) dejan de valer y no se renuevan.

ALTER TABLE public.users ADD COLUMN IF NOT EXISTS sessions_valid_after bigint NOT NULL DEFAULT 0;

-- Verificación: debe aparecer la columna sessions_valid_after.
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = 'users'
  AND column_name = 'sessions_valid_after';
