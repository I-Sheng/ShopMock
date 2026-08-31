-- HR DB schema — people data, isolated from every other ShopMock datastore.
--
-- Two deliberate absences define this schema:
--
--   1. No PostgREST. Unlike catalog/orders/finance/customer, this database has
--      no `authenticator`/`web_anon` pair and no HTTP service in front of it.
--      The only thing that ever connects is hr-portal, as the read-only
--      `hr_portal` login created in 03_hr_portal_role.sh.
--   2. No government identifiers, no home addresses, no bank details, no dates
--      of birth. A staff directory does not need them, and a lab dataset that
--      does not hold them cannot leak them. Compensation is stored because
--      departmental payroll is a real HR figure — but the roster query never
--      selects it and the serializer refuses to publish it per person.
--
-- All data is synthetic (see 02_seed.sql). No real person is represented.

CREATE SCHEMA hr;

CREATE TABLE hr.departments (
    id               bigserial PRIMARY KEY,
    name             text NOT NULL UNIQUE,
    cost_center      text NOT NULL,
    headcount_budget int  NOT NULL DEFAULT 0
);

CREATE TABLE hr.employees (
    id                bigserial PRIMARY KEY,
    employee_no       text NOT NULL UNIQUE,        -- internal staff reference
    first_name        text NOT NULL,
    last_name         text NOT NULL,
    work_email        text NOT NULL UNIQUE,        -- work address only
    job_title         text NOT NULL,
    department_id     bigint REFERENCES hr.departments(id),
    employment_type   text NOT NULL DEFAULT 'full_time',  -- full_time | part_time | contract
    status            text NOT NULL DEFAULT 'active',     -- active | on_leave | left
    hired_on          date NOT NULL,
    -- Monthly gross, in cents. Aggregated per department by the portal; never
    -- selected by the roster query and never serialized per person.
    base_salary_cents bigint NOT NULL DEFAULT 0
);

CREATE INDEX ON hr.employees (department_id);
CREATE INDEX ON hr.employees (status);

CREATE TABLE hr.leave_requests (
    id           bigserial PRIMARY KEY,
    employee_id  bigint NOT NULL REFERENCES hr.employees(id),
    kind         text NOT NULL,                    -- vacation | sick | parental | unpaid
    starts_on    date NOT NULL,
    ends_on      date NOT NULL,
    days         int  NOT NULL,
    status       text NOT NULL DEFAULT 'pending',  -- pending | approved | declined
    submitted_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT leave_dates_ordered CHECK (ends_on >= starts_on)
);

CREATE INDEX ON hr.leave_requests (employee_id);
