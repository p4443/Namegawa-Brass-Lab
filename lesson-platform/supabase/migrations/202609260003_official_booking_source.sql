alter table public.lesson_bookings
  add column if not exists official_reservation_id text unique;

alter table public.lesson_bookings
  alter column cal_booking_id drop not null;

alter table public.lesson_bookings
  drop constraint if exists lesson_bookings_source_check;

alter table public.lesson_bookings
  add constraint lesson_bookings_source_check
  check (num_nonnulls(cal_booking_id, official_reservation_id) = 1);
