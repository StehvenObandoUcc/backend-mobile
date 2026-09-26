-- ==============================================================================
-- Migración 01: Habilitar Row Level Security (RLS) en Food AI (Supabase Cloud)
-- Proyecto: awrfcseabnekjmrcjdyo
-- ==============================================================================

-- 1. Eliminar dinámicamente TODAS las políticas previas en las tablas del proyecto
DO $$
DECLARE
    pol record;
BEGIN
    FOR pol IN (
        SELECT policyname, tablename
        FROM pg_policies
        WHERE schemaname = 'public' 
          AND tablename IN ('recipes', 'shopping_items', 'ingredients', 'users')
    ) LOOP
        EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', pol.policyname, pol.tablename);
    END LOOP;
END $$;

-- 2. Habilitar y FORZAR Row Level Security (RLS) en todas las tablas
ALTER TABLE public.recipes ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.recipes FORCE ROW LEVEL SECURITY;

ALTER TABLE public.shopping_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.shopping_items FORCE ROW LEVEL SECURITY;

ALTER TABLE public.ingredients ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ingredients FORCE ROW LEVEL SECURITY;

ALTER TABLE public.users ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.users FORCE ROW LEVEL SECURITY;

-- 3. Revocar permisos de escritura directos al rol 'anon'
REVOKE INSERT, UPDATE, DELETE ON public.recipes FROM anon;
REVOKE INSERT, UPDATE, DELETE ON public.shopping_items FROM anon;
REVOKE INSERT, UPDATE, DELETE ON public.ingredients FROM anon;
REVOKE INSERT, UPDATE, DELETE ON public.users FROM anon;

-- 4. Crear políticas estrictas de aislamiento por user_id para usuarios autenticados
CREATE POLICY "Users can only access own recipes" ON public.recipes
    FOR ALL
    TO authenticated
    USING (auth.uid()::text = user_id)
    WITH CHECK (auth.uid()::text = user_id);

CREATE POLICY "Users can only access own shopping items" ON public.shopping_items
    FOR ALL
    TO authenticated
    USING (auth.uid()::text = user_id)
    WITH CHECK (auth.uid()::text = user_id);

CREATE POLICY "Users can only access own ingredients" ON public.ingredients
    FOR ALL
    TO authenticated
    USING (auth.uid()::text = user_id)
    WITH CHECK (auth.uid()::text = user_id);

CREATE POLICY "Users can only access own profile" ON public.users
    FOR ALL
    TO authenticated
    USING (auth.uid()::text = id)
    WITH CHECK (auth.uid()::text = id);

-- 5. Consulta de verificación final (muestra las políticas activas y estado RLS)
SELECT tablename, policyname, roles, cmd 
FROM pg_policies 
WHERE schemaname = 'public' 
  AND tablename IN ('recipes', 'shopping_items', 'ingredients', 'users');
