alter table public.lesson_bookings
  alter column guardian_line_user_id drop not null;

create table if not exists public.booking_claim_challenges (
  id uuid primary key default gen_random_uuid(),
  official_reservation_id text not null references public.lesson_bookings(official_reservation_id) on delete cascade,
  guardian_line_user_id text not null references public.guardians(line_user_id) on delete cascade,
  code_digest text not null check (char_length(code_digest) = 64),
  expires_at timestamptz not null,
  attempts smallint not null default 0 check (attempts between 0 and 5),
  used_at timestamptz,
  created_at timestamptz not null default now(),
  unique (official_reservation_id, guardian_line_user_id)
);

alter table public.booking_claim_challenges enable row level security;

revoke all on public.booking_claim_challenges from anon, authenticated;
grant select, insert, update, delete
  on table public.booking_claim_challenges
  to service_role;

create or replace function public.create_booking_claim_challenge(
  p_official_reservation_id text,
  p_guardian_line_user_id text,
  p_code_digest text
)
returns boolean
language plpgsql
security definer
set search_path = public
as $$
declare
  challenge_id uuid;
begin
  insert into public.booking_claim_challenges (
    official_reservation_id,
    guardian_line_user_id,
    code_digest,
    expires_at,
    attempts,
    used_at,
    created_at
  ) values (
    p_official_reservation_id,
    p_guardian_line_user_id,
    p_code_digest,
    now() + interval '10 minutes',
    0,
    null,
    now()
  )
  on conflict (official_reservation_id, guardian_line_user_id) do update
  set code_digest = excluded.code_digest,
      expires_at = excluded.expires_at,
      attempts = 0,
      used_at = null,
      created_at = excluded.created_at
  where booking_claim_challenges.created_at <= now() - interval '1 minute'
  returning id into challenge_id;

  return challenge_id is not null;
end;
$$;

revoke all on function public.create_booking_claim_challenge(text, text, text) from public, anon, authenticated;
grant execute on function public.create_booking_claim_challenge(text, text, text) to service_role;

create or replace function public.claim_official_booking(
  p_official_reservation_id text,
  p_guardian_line_user_id text,
  p_code_digest text
)
returns text
language plpgsql
security definer
set search_path = public
as $$
declare
  challenge public.booking_claim_challenges%rowtype;
  linked_guardian text;
begin
  select * into challenge
  from public.booking_claim_challenges
  where official_reservation_id = p_official_reservation_id
    and guardian_line_user_id = p_guardian_line_user_id
  for update;

  if not found or challenge.used_at is not null or challenge.expires_at <= now() or challenge.attempts >= 5 then
    return 'invalid';
  end if;

  if challenge.code_digest <> p_code_digest then
    update public.booking_claim_challenges
    set attempts = least(attempts + 1, 5)
    where id = challenge.id;
    return 'invalid';
  end if;

  update public.lesson_bookings
  set guardian_line_user_id = p_guardian_line_user_id,
      updated_at = now()
  where official_reservation_id = p_official_reservation_id
    and guardian_line_user_id is null
  returning guardian_line_user_id into linked_guardian;

  if linked_guardian is null then
    select guardian_line_user_id into linked_guardian
    from public.lesson_bookings
    where official_reservation_id = p_official_reservation_id;
    if linked_guardian is distinct from p_guardian_line_user_id then
      return 'already_linked';
    end if;
  end if;

  update public.booking_claim_challenges
  set used_at = now()
  where id = challenge.id;
  return 'claimed';
end;
$$;

revoke all on function public.claim_official_booking(text, text, text) from public, anon, authenticated;
grant execute on function public.claim_official_booking(text, text, text) to service_role;