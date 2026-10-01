alter table public.lesson_bookings
  drop constraint if exists lesson_bookings_status_check;

alter table public.lesson_bookings
  add constraint lesson_bookings_status_check
  check (status in ('予約済み', '確定', '変更', 'キャンセル'));