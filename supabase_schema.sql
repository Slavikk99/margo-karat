-- ============================================================
-- MARGO KARAT — схема базы данных Supabase
-- Выполнить в Supabase → SQL Editor → New query → Run
-- ============================================================

-- ---------- ТАБЛИЦА 1: ПРОФИЛИ ПОЛЬЗОВАТЕЛЕЙ ----------
-- (auth.users создаёт сам Supabase; здесь — расширенный профиль)
create table if not exists public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  name text,
  surname text,
  email text,
  phone text,
  country text,
  birth_date date,
  avatar_url text,
  nickname text,
  free_question_used boolean default false,
  created_at timestamptz default now()
);

-- автосоздание профиля при регистрации
create or replace function public.handle_new_user()
returns trigger language plpgsql security definer as $$
begin
  insert into public.profiles (id, email, name)
  values (new.id, new.email, coalesce(new.raw_user_meta_data->>'name',''));
  return new;
end $$;
drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

-- ---------- ТАБЛИЦА 2: ЗАКАЗЫ УСЛУГ ----------
create table if not exists public.service_orders (
  id uuid primary key default gen_random_uuid(),
  user_id uuid references auth.users(id),
  customer_name text not null,
  customer_surname text,
  customer_email text not null,
  customer_phone text,
  birth_date date,
  package text not null,              -- '1 вопрос' / '3 вопроса' / '5 направлений' / 'FREE QUESTION'
  price numeric(8,2) default 0,
  directions jsonb not null,          -- ["Таро","Каббала",...]
  question text,
  uploaded_images jsonb default '[]', -- ссылки на файлы в Storage
  coffee_mode text,                   -- 'upload' | 'margo_draws' | null
  is_free boolean default false,
  payment_status text default 'ожидает оплаты',  -- ожидает оплаты / оплачен / ошибка
  status text default 'Новый заказ',
  -- Статусы: Новый заказ / В обработке / Ответ создан / Ожидает проверки /
  --          Одобрен / Отправлен клиенту / Ошибка
  generated_response text,
  approval_status text default 'draft',  -- draft / approved / regenerate
  approved_at timestamptz,
  email_sent boolean default false,
  email_sent_at timestamptz,
  stripe_session_id text,
  created_at timestamptz default now()
);

-- ---------- ТАБЛИЦА 3: ПРИВАТНЫЕ КОНСУЛЬТАЦИИ ----------
create table if not exists public.private_consultations (
  id uuid primary key default gen_random_uuid(),
  user_id uuid references auth.users(id),
  name text not null,
  surname text,
  phone text not null,
  email text not null,
  birth_date date,
  message text,
  price numeric(8,2) default 49.99,
  payment_status text default 'ожидает оплаты',
  consultation_status text default 'Новый заказ',
  stripe_session_id text,
  created_at timestamptz default now()
);

-- ---------- ТАБЛИЦА 4: БЕСПЛАТНЫЕ КОДЫ (Telegram-бот) ----------
create table if not exists public.free_codes (
  code text primary key,              -- margo-free-xxxxxxxx-xxxx
  tg_user_id bigint,
  tg_username text,
  ig_nick text,
  screenshots jsonb default '[]',
  used boolean default false,
  used_by uuid references auth.users(id),
  created_at timestamptz default now(),
  expires_at timestamptz default (now() + interval '30 minutes')
);

-- ---------- STORAGE ----------
-- Создать бакеты вручную: Storage → New bucket
--   uploads  (public: НЕТ)  — фото ладоней и кофейной гущи
--   avatars  (public: ДА)   — аватары профилей

-- ---------- БЕЗОПАСНОСТЬ (RLS) ----------
alter table public.profiles enable row level security;
alter table public.service_orders enable row level security;
alter table public.private_consultations enable row level security;
alter table public.free_codes enable row level security;

-- профиль: владелец читает/правит своё
create policy "own profile read"  on public.profiles for select using (auth.uid() = id);
create policy "own profile update" on public.profiles for update using (auth.uid() = id);

-- заказы: владелец видит свои; создавать может любой аутентифицированный
create policy "own orders read" on public.service_orders
  for select using (auth.uid() = user_id);
create policy "insert own orders" on public.service_orders
  for insert with check (auth.uid() = user_id or user_id is null);

create policy "own consult read" on public.private_consultations
  for select using (auth.uid() = user_id);
create policy "insert consult" on public.private_consultations
  for insert with check (true);

-- коды: проверка кода со фронта (чтение по точному совпадению)
create policy "check code" on public.free_codes for select using (true);
create policy "mark code used" on public.free_codes
  for update using (used = false);

-- Админ и AI-агент работают через SERVICE KEY (обходит RLS) — ключ хранить
-- только на сервере (.env), никогда во фронтенде.

-- ---------- ИНДЕКСЫ ----------
create index if not exists idx_orders_status on public.service_orders(status);
create index if not exists idx_orders_payment on public.service_orders(payment_status);
create index if not exists idx_codes_expires on public.free_codes(expires_at);
