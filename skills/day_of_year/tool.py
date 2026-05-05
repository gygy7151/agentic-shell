"""Return the ordinal day of year for a given ISO date string.

Example:
>>> day_of_year('2026-05-05')
125

The function uses the standard library `datetime` and accepts strings
in the format 'YYYY-MM-DD'. It raises a ValueError if the format is
invalid or the date does not exist.
"""

from datetime import datetime

def day_of_year(date_str: str) -> int:
    """Return the day of year for an ISO date string.

    Parameters
    ----------
    date_str: str
        ISO formatted date, e.g. '2026-05-05'.

    Returns
    -------
    int
        Day number within the year (1-366).
    """
    # Parse the string; will raise ValueError if invalid.
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    return dt.timetuple().tm_yday

# Demonstration
if __name__ == "__main__":
    print(day_of_year("2026-05-05"))
