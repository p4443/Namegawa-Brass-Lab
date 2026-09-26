alter table public.lesson_bookings
  add column if not exists line_notified_status text
  check (line_notified_status in ('確定', 'キャンセル'));

update public.lesson_bookings
set line_notified_status = '確定'
where status = '確定'
  and guardian_line_user_id is not null
  and line_notified_status is null;