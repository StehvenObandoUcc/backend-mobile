-- Fase 5: caché persistente de fotos aprobadas por plato (evita repetir búsquedas de Unsplash y visión de DeepSeek).
-- Cómo correrla: Supabase > SQL Editor > New query > pega este archivo > Run.
-- Es idempotente: se puede ejecutar varias veces sin error.
-- Sin datos personales. Solo el backend la usa, con su clave de servicio (service_role, que se salta RLS):
-- RLS queda habilitado y forzado SIN políticas, así que anon y authenticated no pueden leer ni escribir nada.
-- Correrla ANTES de desplegar el backend que la usa; si falta, el backend sigue funcionando (solo no cachea).

CREATE TABLE IF NOT EXISTS public.dish_photos (
    dish_key          text PRIMARY KEY,                 -- dishNameEn normalizado (minúsculas, sin puntuación)
    image_uri         text NOT NULL,                    -- https://images.unsplash.com/...
    image_credit      text NOT NULL,                    -- JSON serializado: {source,name,profileUrl,sourceUrl}, igual que en recipes
    download_location text,                             -- https://api.unsplash.com/... (aviso de descarga de Unsplash)
    rules_version     integer NOT NULL,                 -- versión de la rúbrica de visión; al subirla, las filas viejas se ignoran
    approved_at       timestamptz DEFAULT now()
);

-- Mismo patrón de 01_enable_rls.sql: RLS habilitado y forzado, y sin políticas.
ALTER TABLE public.dish_photos ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.dish_photos FORCE ROW LEVEL SECURITY;
REVOKE ALL ON public.dish_photos FROM anon, authenticated;

-- Verificación 1: deben aparecer las 6 columnas.
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = 'dish_photos'
ORDER BY ordinal_position;

-- Verificación 2: rls_enabled y rls_forced en true.
SELECT relname AS tabla, relrowsecurity AS rls_enabled, relforcerowsecurity AS rls_forced
FROM pg_class
WHERE oid = 'public.dish_photos'::regclass;

-- Verificación 3: no debe devolver ninguna fila (la tabla no tiene políticas).
SELECT policyname, roles, cmd
FROM pg_policies
WHERE schemaname = 'public' AND tablename = 'dish_photos';
