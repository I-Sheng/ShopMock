"""Every SQL statement this service can issue.

They are module constants, never assembled from request input, and they are
collected in `ALL_STATEMENTS` so the test suite can assert properties of the
whole set: no `SELECT *`, no schema but `hr`, and no mutating verb. A statement
that is not in that mapping does not exist.

The row limit is the only caller-influenced value and it never reaches the SQL
text — it is clamped to `HR_MAX_ROWS` and passed as a bound parameter.

Isolation, restated: this module runs on the single connection declared in
hr_portal/settings.py, authenticated as `hr_portal` against hr-db, which is a
separate Postgres instance on its own internal network with no PostgREST in
front of it. There is no connection here on which a query against another
domain could run.

One asymmetry worth naming: the roster statement does not select individual pay
at all, while the department statement sums it. A total answers the budgeting
question; the per-person figure is the thing worth not having in a response.
"""
from django.conf import settings
from django.db import connections

EMPLOYEES = """
    SELECT e.id, e.employee_no, e.first_name, e.last_name, e.work_email,
           e.job_title, e.employment_type, e.status, e.hired_on,
           d.name AS department
      FROM hr.employees e
      LEFT JOIN hr.departments d ON d.id = e.department_id
     ORDER BY e.last_name, e.first_name, e.id
     LIMIT %s
"""

DEPARTMENTS = """
    SELECT d.id, d.name, d.cost_center, d.headcount_budget,
           count(e.id) FILTER (WHERE e.status = 'active') AS headcount,
           coalesce(
               sum(e.base_salary_cents) FILTER (WHERE e.status = 'active'), 0
           ) AS payroll_cents
      FROM hr.departments d
      LEFT JOIN hr.employees e ON e.department_id = d.id
     GROUP BY d.id, d.name, d.cost_center, d.headcount_budget
     ORDER BY d.name
"""

LEAVE_REQUESTS = """
    SELECT l.id, e.employee_no, e.first_name, e.last_name,
           l.kind, l.starts_on, l.ends_on, l.days, l.status
      FROM hr.leave_requests l
      JOIN hr.employees e ON e.id = l.employee_id
     ORDER BY l.starts_on DESC, l.id DESC
     LIMIT %s
"""

HEADCOUNT = """
    SELECT status, count(*) AS headcount
      FROM hr.employees
     GROUP BY status
     ORDER BY status
"""

ALL_STATEMENTS = {
    'employees': EMPLOYEES,
    'departments': DEPARTMENTS,
    'leave_requests': LEAVE_REQUESTS,
    'headcount': HEADCOUNT,
}


def clamp_rows(requested):
    """Clamp a caller-supplied row count into [1, HR_MAX_ROWS].

    Anything unparseable — absent, empty, or a hopeful `1;DROP TABLE` — falls
    back to the configured maximum rather than to the caller's intent. The view
    calls this before the query does, and the query calls it again.
    """
    cap = settings.HR_MAX_ROWS
    try:
        value = int(requested)
    except (TypeError, ValueError):
        return cap
    return max(1, min(value, cap))


def _rows(sql, params=None):
    with connections['default'].cursor() as cursor:
        cursor.execute(sql, params if params is not None else [])
        columns = [column[0] for column in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]


def employees(limit):
    return _rows(EMPLOYEES, [clamp_rows(limit)])


def leave_requests(limit):
    return _rows(LEAVE_REQUESTS, [clamp_rows(limit)])


def departments():
    return _rows(DEPARTMENTS)


def headcount():
    return {row['status']: row['headcount'] for row in _rows(HEADCOUNT)}
