-- Fase 3b: foto de la receta (Unsplash) y crédito del fotógrafo.
-- Cómo correrla: Supabase > SQL Editor > New query > pega este archivo > Run.
-- Es idempotente: se puede ejecutar varias veces sin error.

ALTER TABLE public.recipes ADD COLUMN IF NOT EXISTS image_uri TEXT;
ALTER TABLE public.recipes ADD COLUMN IF NOT EXISTS image_credit TEXT; -- JSON serializado: {source,name,profileUrl,sourceUrl}

-- Verificación: deben aparecer las dos columnas (image_uri, image_credit).
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = 'recipes'
  AND column_name IN ('image_uri', 'image_credit');
