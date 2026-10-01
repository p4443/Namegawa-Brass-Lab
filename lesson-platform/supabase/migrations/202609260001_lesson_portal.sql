create extension if not exists pgcrypto;

create table if not exists public.guardians (
  id uuid primary key default gen_random_uuid(),
  line_user_id text not null unique,
  display_name text not null check (char_length(display_name) between 1 and 80),
  last_login_at timestamptz not null default now(),
  created_at timestamptz not null default now()
);

create table if not exists public.lesson_bookings (
  id uuid primary key default gen_random_uuid(),
  cal_booking_id text not null unique,
  guardian_line_user_id text not null references public.guardians(line_user_id) on delete cascade,
  lesson_type text not null default 'トランペットレッスン',
  starts_at timestamptz not null,
  ends_at timestamptz,
  status text not null check (status in ('予約済み', '変更', 'キャンセル')),
  updated_at timestamptz not null default now(),
  created_at timestamptz not null default now()
);

create index if not exists lesson_bookings_guardian_starts_idx
  on public.lesson_bookings (guardian_line_user_id, starts_at);

alter table public.guardians enable row level security;
alter table public.lesson_bookings enable row level security;

revoke all on public.guardians from anon, authenticated;
revoke all on public.lesson_bookings from anon, authenticated;
