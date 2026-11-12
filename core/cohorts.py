"""Source-audited overlays. Never edit scraped data or infer caste, religion or age limits.

These rules supplement, never replace, existing official predicates. See
docs/HOTFIX_V25_AUDIT.md for sources and the exact missing extraction evidence.
"""
from copy import deepcopy
import re

MEAL_IDS = {'school_nutrition_scheme', 'mid_day_meal_scheme_shaleya_poshan_aahar_yojana'}
NAVODAYA_IDS = {'jawahar_navodaya_vidyalaya'}
SCHOOL_LEVELS = {'class_1_to_8', 'class_9_to_10', 'class_11_to_12'}
COLLEGE_LEVELS = {'iti_or_diploma', 'undergraduate', 'postgraduate', 'phd'}


def school_program(scheme):
    if scheme['id'] in MEAL_IDS:
        return 'school_nutrition_beed'
    if scheme['id'] in NAVODAYA_IDS:
        return 'navodaya_class_6_entry'
    return None


def effective_scheme(scheme):
    """Return an independent overlay; raw catalog, mapped rules and URLs stay intact."""
    result = deepcopy(scheme)
    program = school_program(scheme)
    if not program:
        return result
    specs = result['eligibility'].setdefault('all', [])
    def rule(field, op, value, reason):
        spec={'field':field, 'op':op, 'value':value, 'reason':reason}
        if spec not in specs:
            specs.append(spec)
    def manual(text):
        criteria=result.setdefault('unmapped_criteria',[])
        if text not in criteria:
            criteria.append(text)
    rule('is_student', 'eq', True, 'Must currently be enrolled in school')
    if program == 'school_nutrition_beed':
        source = 'https://www.zpbeed.gov.in/en/scheme/school-nutrition-scheme/'
        rule('current_study_level','eq','class_1_to_8','Source describes primary/upper-primary classes 1–8')
        rule('enrolled_grade','gte',1,'School nutrition source: classes 1–8')
        rule('enrolled_grade','lte',8,'School nutrition source: classes 1–8')
        rule('school_management','in',['government_local_body','aided','partially_aided','listed_education_centre'],
             'Enrollment must be in a school/centre type listed by the official source')
    else:
        source = 'https://zpamravati.gov.in/en/scheme/jawahar-navodaya-vidyalaya/'
        rule('current_study_level','eq','class_1_to_8','This entry describes a Class 5 pupil seeking Class 6 admission')
        rule('enrolled_grade','eq',5,'The official entry requires currently studying in 5th standard')
        manual('Verify current admission-year prospectus, district/school conditions, selection examination and application dates; this dated district page is not a complete prospectus.')
    manual('Confirm current availability and geographic/application conditions with the official authority; source extraction is not a complete eligibility audit.')
    result['beneficiary_cohort'] = {'kind':'school_only', 'program':program, 'source_url':source,
                                    'audited_on':'2026-10-30', 'complete':False}
    return result


def excluded_school_default(scheme, profile):
    title=scheme.get('name','').casefold()
    title_school_only=bool(re.search(r'\b(?:primary schools?|secondary schools?|pre[- ]matric|school meals?|school nutrition)\b',title))
    if not school_program(scheme) and not title_school_only:
        return False
    level = profile.get('current_study_level')
    if level in COLLEGE_LEVELS or profile.get('is_student') is False:
        return True
    if profile.get('occupation') == 'retired' and level not in SCHOOL_LEVELS:
        return True
    # This filters default recommendations, not eligibility. Explicit school enrollment
    # is allowed at any age, including adult education; no invented age threshold rule.
    return (profile.get('age',0) >= 18 and level not in SCHOOL_LEVELS
            and profile.get('enrolled_grade') is None)
