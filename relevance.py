"""Decides whether a post really announces a data/AI job, and how useful it is.

LinkedIn's Posts search matches words anywhere in a post, so a search for
hiring + "AI quality" also returns thought pieces, course ads, and gig-work
referrals. A post is relevant only if a hiring phrase ("my team is hiring",
"I'm looking for") and a target job title appear close together, and it isn't
gig work, agency staffing, or a list of other companies' jobs.
"""

from __future__ import annotations

import re
import unicodedata

from scraper import Post

# Job titles worth an email. Edit this list to change what counts.
TARGET_ROLES: tuple[str, ...] = (
    r"data scien(?:tists?|ce)",
    r"data engineer(?:s|ing)?",
    r"analytics engineer(?:s|ing)?",
    r"data analysts?",
    r"(?:machine learning|ml|mlops|ai|gen ?ai|generative ai|ai/ml|ml/ai|llm)[ /-]+(?:\w+[ /-]+){0,2}"
    r"(?:engineers?|scientists?|researchers?)",
    r"(?:applied|research|decision) scientists?",
    r"(?:ai|llm|model) (?:evaluations?|evals?|quality)(?: \w+)? (?:engineers?|scientists?|analysts?|leads?|managers?)",
    r"(?:ai|applied) science teams?",
    r"evals? (?:engineers?|scientists?|leads?)",
)
ROLE_RE = re.compile(r"(?<![a-z0-9])(?:" + "|".join(TARGET_ROLES) + r")(?![a-z0-9])", re.I)

_SELF = r"(?:i['’]?m|i am|we['’]?re|we are|my team(?: (?:at|@) [^.!?\n]{1,40}?)?(?: and i)? (?:is|are)|our team is)"
HIRING_RE = re.compile(
    rf"{_SELF}\s+(?:currently |now |also |actively |still |once again |thrilled to be |excited to be )?"
    r"(?:#?hiring|recruiting|looking (?:for|to (?:hire|add))|searching for|seeking|growing|expanding)"
    r"|\b(?:join|grow|growing|expanding|on|into|to) my\b(?: own)?(?: \w+){0,4} team"
    r"|\bmy\b(?: \w+){0,3} team[^.!?]{0,40}(?:is|are) (?:#?hiring|looking|growing)"
    r"|(?-i:\b[A-Z][\w&.]+) is (?:currently |now |also |actively )?#?hiring"
    r"|#?hiring\s*(?::|\||-|–|!|for\b|an?\b|#|🚀|now)"
    r"|\bcome (?:work|join)\b|\bopen (?:role|position|req)s?\b|\b(?:new |job )opening",
    re.I,
)
FIRST_PERSON_RE = re.compile(
    r"\bmy\b(?: own)?(?: \w+){0,4} team|\bi['’]?m (?:\w+ )?(?:hiring|looking for|growing)|"
    r"\bi am (?:\w+ )?(?:hiring|looking for)|\breport(?:ing|s)? to me\b|\bwork(?:ing)? (?:directly )?with me\b",
    re.I,
)
# If one of these is named first, that is the job being filled, not the target role.
OTHER_JOB_RE = re.compile(
    r"\b(?:product|project|program|programme|account|marketing|office|engagement) (?:manager|owner|lead)s?\b|"
    r"\brecruiters?\b|\bhead of (?!data|ai|ml|machine)|\bdesigners?\b|\baccount executives?\b|\bsales\b",
    re.I,
)
HIRING_WINDOW = 120  # max characters between the hiring phrase and the role

GIG_RE = re.compile(
    r"\b(?:data )?annotat(?:ion|or)s?\b|\bai (?:trainer|tutor|rater)s?\b|\brubric|"
    r"\bper (?:hour|task)\b|/\s*(?:hr|hour|task)\b|\bhourly (?:contract|independent)|\bfreelance (?:project|role|opportunit)|"
    r"\bindependent contractor|\b(?:ai|model) training (?:project|expert)|\breferral (?:link|bonus)|"
    r"\btalent network\b|\bcrowd ?work",
    re.I,
)
# These mostly appear in gig referrals; their own staff name them in the headline.
GIG_PLATFORM_RE = re.compile(r"\b(?:mercor|micro1|alignerr|oouraa|outlier ai)\b", re.I)
AGENCY_HEADLINE_RE = re.compile(
    r"\bus(?:a)? (?:it|&|and|staffing|recruit|hiring|market|tech)|\b(?:sr\.?|senior|lead)? ?(?:us )?it (?:& non[- ]it )?"
    r"recruit|\bstaffing\b|\bbench sales\b|\bhead ?hunt|\btalent agent\b|\brecruit$|\bworkforce solutions\b|\bconsultant \|.{0,40}talent|\brecruiting consultant|\brecruitment (?:partner|agency|consultant|"
    r"& solutions)|\bopt\b|\bcontract hiring\b|\b(?:executive|agency) search\b|\bmatching .{0,30}talent\b|"
    r"\bconnecting .{0,40}(?:talent|professionals|opportunit)|\bhelping .{0,40}(?:hire|scale with .{0,20}talent)",
    re.I,
)
AGENCY_TEXT_RE = re.compile(
    r"\bmy client\b|\bour client\b|\bclient of (?:ours|mine)\b|\bon behalf of\b|\bi['’]?m partner(?:ing|ed) with\b|"
    r"\bvisa(?: status)?\s*[:–-]|\busc\b|\bgc\b|\bh-?1b transfer|\b(?:c2c|c2h|w2)\b|\bcontract (?:role|position|duration)|"
    r"\bhire type\s*:\s*contract|\bduration\s*:|\b(?:employment|engagement|job) ?(?:type)?\s*:?\s*contract\b|\bsend (?:your |me )?(?:updated )?resumes? (?:to|at)\b|"
    r"\bshare (?:your |updated )?(?:cv|resumes?)\b.{0,30}@|\bmonths? contract\b",
    re.I,
)
# People who post other companies' jobs for reach: coaches, "daily job links".
CREATOR_RE = re.compile(
    r"\bcareer coach|\bresume (?:writer|software)|\bjob links?\b|\bhelping (?:you|grads|students|job ?seekers|freshers)|"
    r"\bland (?:your|their) (?:dream )?job|\btalent referrals?\b|\bremote work mentor",
    re.I,
)
LISTICLE_RE = re.compile(
    r"\b(?:these|here are|top) \d+ (?:\w+ ){0,3}(?:companies|jobs|roles|startups)|\band more are hiring|"
    r"\bcompanies (?:are )?(?:currently |actively )?hiring|\bcomment .{0,30}(?:and i['’]?ll|to get|for the link)|"
    r"\bi share .{0,60}(?:every ?day|daily)|\bthese companies (?:are|have)|"
    r"\bfollow (?:me )?for (?:more|daily)|\bjob alert|\bfreshers?\b",
    re.I,
)
RECRUITER_RE = re.compile(r"recruit|talent|sourc(?:er|ing)|\bhr\b|human resources|people (?:ops|operations|partner)|staffing", re.I)


def _norm(text: str) -> str:
    """Plain letters for text posted in styled Unicode ("𝗛𝗶𝗿𝗶𝗻𝗴")."""
    return unicodedata.normalize("NFKC", text)


def announces_target_role(text: str) -> bool:
    """True if a hiring phrase and a target job title are close together."""
    text = _norm(text)
    roles = [m.span() for m in ROLE_RE.finditer(text)]
    if not roles:
        return False
    for n, h in enumerate(HIRING_RE.finditer(text)):
        # "I'm hiring a Product Manager ... with our data scientists": the first
        # title after the first hiring phrase is the job.
        other = OTHER_JOB_RE.search(text, h.end(), h.end() + HIRING_WINDOW) if n == 0 else None
        if other and not any(h.start() <= start < other.start() for start, _ in roles):
            return False
        for start, end in roles:
            gap = start - h.end() if start >= h.end() else h.start() - end
            # A title after the phrase is normal; before it, only right before.
            if gap > (HIRING_WINDOW if start >= h.start() else 40):
                continue
            if start >= h.end() and OTHER_JOB_RE.search(text, h.end(), start):
                continue
            return True
    return False


def reject_reason(post: Post) -> str:
    """Why a post isn't worth emailing, or "" if it is."""
    headline, text = _norm(post.headline), _norm(post.text)
    if GIG_RE.search(f"{headline} {text}"):
        return "gig work"
    if GIG_PLATFORM_RE.search(text) and not GIG_PLATFORM_RE.search(headline):
        return "gig work"
    if AGENCY_HEADLINE_RE.search(headline) or AGENCY_TEXT_RE.search(text):
        return "agency/contract"
    if CREATOR_RE.search(headline) or LISTICLE_RE.search(text):
        return "job-list post"
    if not announces_target_role(text):
        return "no target role being hired"
    return ""


def is_hiring_manager(post: Post) -> bool:
    """The author seems to be hiring for their own team, not recruiting."""
    return not RECRUITER_RE.search(_norm(post.headline)) and bool(FIRST_PERSON_RE.search(_norm(post.text)))
