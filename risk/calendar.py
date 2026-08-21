"""QuantLib date/calendar wrapper - the only module besides montecarlo.py and
covariance.py that touches QuantLib directly. Converts at the boundary: takes
and returns Python `date`/`datetime`, never leaks `ql.Date` to callers.
"""

from datetime import date, datetime

import QuantLib as ql

NYSE = ql.UnitedStates(ql.UnitedStates.NYSE)


def _to_ql(d) -> ql.Date:
    if isinstance(d, datetime):
        d = d.date()
    return ql.Date(d.day, d.month, d.year)


def _from_ql(qd: ql.Date) -> date:
    return date(qd.year(), qd.month(), qd.dayOfMonth())


def is_business_day(d) -> bool:
    return NYSE.isBusinessDay(_to_ql(d))


def next_business_day(d) -> date:
    qd = _to_ql(d)
    if not NYSE.isBusinessDay(qd):
        qd = NYSE.adjust(qd, ql.Following)
    return _from_ql(qd)


def add_business_days(d, n: int) -> date:
    return _from_ql(NYSE.advance(_to_ql(d), ql.Period(n, ql.Days)))


def business_days_between(start, end) -> int:
    """Count of business days in (start, end], i.e. how many sessions occur
    if you start counting the day after `start`."""
    return NYSE.businessDaysBetween(_to_ql(start), _to_ql(end))
