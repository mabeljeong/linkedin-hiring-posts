"""Best-effort US/non-US classification of a post from its text and headline.

LinkedIn Posts search has no location filter, so this looks for location
mentions in the post. Returns "us", "non-us", or "unknown".
"""

from __future__ import annotations

import re

US_STATES = (
    "Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado",
    "Connecticut", "Delaware", "Florida", "Georgia", "Hawaii", "Idaho",
    "Illinois", "Indiana", "Iowa", "Kansas", "Kentucky", "Louisiana",
    "Maine", "Maryland", "Massachusetts", "Michigan", "Minnesota",
    "Mississippi", "Missouri", "Montana", "Nebraska", "Nevada",
    "New Hampshire", "New Jersey", "New Mexico", "New York",
    "North Carolina", "North Dakota", "Ohio", "Oklahoma", "Oregon",
    "Pennsylvania", "Rhode Island", "South Carolina", "South Dakota",
    "Tennessee", "Texas", "Utah", "Vermont", "Virginia", "Washington",
    "West Virginia", "Wisconsin", "Wyoming",
)

US_CITIES = (
    "San Francisco", "Bay Area", "SF Bay", "Silicon Valley", "San Jose",
    "Palo Alto", "Mountain View", "Sunnyvale", "Menlo Park", "Oakland",
    "Los Angeles", "San Diego", "Seattle", "Bellevue", "Portland", "Denver",
    "Boulder", "Austin", "Dallas", "Houston", "Chicago", "Boston",
    "Cambridge, MA", "NYC", "Manhattan", "Brooklyn", "Jersey City",
    "Philadelphia", "Pittsburgh", "Atlanta", "Miami", "Tampa", "Charlotte",
    "Raleigh", "Durham", "Nashville", "Phoenix", "Salt Lake City",
    "Minneapolis", "Detroit", "Columbus", "Cincinnati", "St. Louis",
    "Kansas City", "Omaha", "Lincoln, NE", "Washington, DC", "Washington DC",
    "D.C.", "Arlington", "Baltimore", "Richmond",
)

# Case-sensitive tokens (would false-match as ordinary words otherwise).
US_TOKENS_CS = ("US", "USA", "U.S.", "U.S.A.", "USA-based", "US-based")
US_PHRASES = ("United States", "Remote US", "Remote - US", "Remote (US)", "US Remote", "anywhere in the US")
US_STATE_ABBREV_RE = re.compile(
    r",\s*(AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|"
    r"MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|"
    r"VA|WA|WV|WI|WY|DC)\b"
)

NON_US_PLACES = (
    "India", "Bangalore", "Bengaluru", "Hyderabad", "Pune", "Chennai",
    "Gurgaon", "Gurugram", "Noida", "Mumbai", "Delhi", "Kolkata", "Ahmedabad",
    "London", "United Kingdom", "England", "Scotland", "Manchester", "Edinburgh",
    "Ireland", "Dublin", "Canada", "Toronto", "Vancouver", "Montreal", "Ottawa",
    "Calgary", "Germany", "Berlin", "Munich", "Frankfurt", "Hamburg",
    "France", "Paris", "Netherlands", "Amsterdam", "Spain", "Madrid",
    "Barcelona", "Portugal", "Lisbon", "Italy", "Milan", "Switzerland",
    "Zurich", "Sweden", "Stockholm", "Denmark", "Copenhagen", "Norway",
    "Finland", "Belgium", "Brussels", "Poland", "Warsaw", "Krakow", "Kraków",
    "Romania", "Bucharest", "Czech", "Prague", "Hungary", "Budapest",
    "Ukraine", "Kyiv", "Israel", "Tel Aviv", "Dubai", "Abu Dhabi",
    "Saudi", "Riyadh", "Egypt", "Cairo", "Nigeria", "Lagos", "Kenya",
    "Nairobi", "South Africa", "Cape Town", "Johannesburg", "Singapore",
    "Malaysia", "Kuala Lumpur", "Indonesia", "Jakarta", "Philippines",
    "Manila", "Vietnam", "Thailand", "Bangkok", "Japan", "Tokyo", "Korea",
    "Seoul", "China", "Shanghai", "Beijing", "Shenzhen", "Hong Kong",
    "Taiwan", "Taipei", "Australia", "Sydney", "Melbourne", "New Zealand",
    "Auckland", "Brazil", "São Paulo", "Sao Paulo", "Mexico City",
    "Guadalajara", "Argentina", "Buenos Aires", "Colombia", "Bogotá",
    "Bogota", "Medellín", "Medellin", "Chile", "Santiago", "Costa Rica",
    "Europe", "European", "Eindhoven", "Pekanbaru", "Uzbekistan", "Kazakhstan", "Kuala Lumpur", "LatAm",
    "Oslo", "Helsinki", "Vienna", "Austria", "Münster", "Cologne", "Luxembourg",
    "Greece", "Athens", "Serbia", "Belgrade", "Bulgaria", "Sofia", "Turkey", "Istanbul",
    "Qatar", "Doha", "Kuwait", "Bahrain", "Oman", "Jordan", "Ghana", "Accra",
    "Jaipur", "Indore", "Coimbatore", "Kochi", "Trivandrum", "Thane", "Vadodara",
    "Islamabad", "Pakistan", "Karachi", "Lahore", "Bangladesh", "Dhaka", "Sri Lanka",
)
# Pay and hiring terms only used outside the US.
NON_US_TERMS_RE = re.compile(r"[₹€£]|[а-яА-Я]{4}|\bLPA\b|\blakhs?\b|\bCTC\b|\bnotice period\b|\bINR\b", re.I)
# "JOIN US" / "with US" in capitals is a pronoun, not a country.
PRONOUN_US_RE = re.compile(r"\b(?:JOIN|WITH|CONTACT|ABOUT|TO|FOR|FOLLOW|WORK WITH) US\b")
NON_US_TOKENS_CS = ("UK", "UAE", "EMEA", "APAC", "LATAM", "EU", "ANZ")


def _word_re(words: tuple[str, ...], flags: int = 0) -> re.Pattern:
    alts = "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))
    return re.compile(rf"(?<![A-Za-z0-9])(?:{alts})(?![A-Za-z0-9])", flags)


_US_RE = _word_re(US_STATES + US_CITIES + US_PHRASES, re.I)
_US_CS_RE = _word_re(US_TOKENS_CS)
_NON_US_RE = _word_re(NON_US_PLACES, re.I)
_NON_US_CS_RE = _word_re(NON_US_TOKENS_CS)
# "Mexico" alone is non-US, but not as part of "New Mexico".
_MEXICO_RE = re.compile(r"(?<!New )(?<![A-Za-z])Mexico(?![A-Za-z])", re.I)


def classify_location(text: str) -> str:
    text = PRONOUN_US_RE.sub(" ", text)
    # A stated "Location: ..." beats places mentioned elsewhere in the post.
    stated = re.search(r"(?:\blocations?\b\s*[:\-–|]+|\bcandidates based in\b)\s*(.{3,60}?)(?:[.;|•]|$)", text, re.I)
    if stated:
        region = classify_location(stated.group(1))
        if region != "unknown":
            return region
    non_us = bool(_NON_US_RE.search(text) or _NON_US_CS_RE.search(text) or _MEXICO_RE.search(text))
    if _US_RE.search(text) or _US_CS_RE.search(text):
        return "us"
    # ", IN" / ", DE" are also country codes, so they don't outweigh a named foreign city.
    if US_STATE_ABBREV_RE.search(text) and not non_us:
        return "us"
    if non_us or NON_US_TERMS_RE.search(text):
        return "non-us"
    return "unknown"
