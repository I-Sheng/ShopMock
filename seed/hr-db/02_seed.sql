-- Synthetic HR seed data. Lab only — every person here is invented, and the
-- work email domain is the lab's own shopmock.lab. No real employee record,
-- no government identifier and no bank detail appears anywhere in this file.

INSERT INTO hr.departments (name, cost_center, headcount_budget) VALUES
  ('People Operations', 'CC-100', 4),
  ('Engineering',       'CC-200', 12),
  ('Customer Support',  'CC-300', 8),
  ('Finance Operations','CC-400', 5);

INSERT INTO hr.employees
  (employee_no, first_name, last_name, work_email, job_title, department_id,
   employment_type, status, hired_on, base_salary_cents)
VALUES
  ('E-0001', 'Rosa',   'Marek',    'rosa.marek@shopmock.lab',
   'People Partner',          1, 'full_time', 'active',   DATE '2023-04-03', 720000),
  ('E-0002', 'Nadia',  'Okonjo',   'nadia.okonjo@shopmock.lab',
   'Head of People',          1, 'full_time', 'active',   DATE '2022-01-17', 980000),
  ('E-0003', 'Tomas',  'Lindqvist','tomas.lindqvist@shopmock.lab',
   'Staff Engineer',          2, 'full_time', 'active',   DATE '2021-09-06', 1150000),
  ('E-0004', 'Priya',  'Raghavan', 'priya.raghavan@shopmock.lab',
   'Platform Engineer',       2, 'full_time', 'active',   DATE '2024-02-12', 890000),
  ('E-0005', 'Denis',  'Baptiste', 'denis.baptiste@shopmock.lab',
   'Frontend Engineer',       2, 'full_time', 'on_leave', DATE '2023-11-20', 840000),
  ('E-0006', 'Mei',    'Zhou',     'mei.zhou@shopmock.lab',
   'Support Lead',            3, 'full_time', 'active',   DATE '2022-06-27', 690000),
  ('E-0007', 'Oscar',  'Villalba', 'oscar.villalba@shopmock.lab',
   'Support Specialist',      3, 'part_time', 'active',   DATE '2025-03-10', 380000),
  ('E-0008', 'Hannah', 'Weir',     'hannah.weir@shopmock.lab',
   'Financial Analyst',       4, 'full_time', 'active',   DATE '2024-08-19', 810000),
  ('E-0009', 'Ivan',   'Petrov',   'ivan.petrov@shopmock.lab',
   'Payroll Coordinator',     4, 'contract',  'active',   DATE '2025-01-06', 560000),
  ('E-0010', 'Grace',  'Adeyemi',  'grace.adeyemi@shopmock.lab',
   'Recruiter',               1, 'contract',  'left',     DATE '2023-02-01', 0);

INSERT INTO hr.leave_requests
  (employee_id, kind, starts_on, ends_on, days, status)
VALUES
  (5, 'parental', DATE '2026-06-01', DATE '2026-09-30', 87, 'approved'),
  (1, 'vacation', DATE '2026-07-06', DATE '2026-07-10',  5, 'approved'),
  (3, 'vacation', DATE '2026-08-17', DATE '2026-08-28', 10, 'pending'),
  (6, 'sick',     DATE '2026-06-22', DATE '2026-06-23',  2, 'approved'),
  (7, 'unpaid',   DATE '2026-09-14', DATE '2026-09-18',  5, 'declined'),
  (4, 'vacation', DATE '2026-12-21', DATE '2026-12-31',  7, 'pending');
