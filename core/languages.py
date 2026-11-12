"""22 scheduled languages plus English. Registry membership is not verified coverage."""
ROWS = [('en','English'),('as','অসমীয়া'),('bn','বাংলা'),('brx','बड़ो'),('doi','डोगरी'),
        ('gu','ગુજરાતી'),('hi','हिन्दी'),('kn','ಕನ್ನಡ'),('ks','کٲشُر'),('kok','कोंकणी'),
        ('mai','मैथिली'),('ml','മലയാളം'),('mni','মণিপুরী'),('mr','मराठी'),('ne','नेपाली'),
        ('or','ଓଡ଼ିଆ'),('pa','ਪੰਜਾਬੀ'),('sa','संस्कृतम्'),('sat','ᱥᱟᱱᱛᱟᱲᱤ'),('sd','سنڌي'),
        ('ta','தமிழ்'),('te','తెలుగు'),('ur','اردو')]
PRIMARY = {'en','hi','mr','bn','ta','te','kn','ml','gu','pa'}
TTS = PRIMARY | {'or'}


def normalize(code):
    short = str(code or 'auto').split('-')[0].lower()
    return 'or' if short == 'od' else short if short in dict(ROWS) else 'auto'


def registry():
    return [{'code': code + '-IN', 'short': code, 'name': name,
             'text': 'fixture_checked' if code in {'en','hi','mr'} else 'unverified_english_fallback',
             'browser_stt': 'device_dependent', 'sarvam_stt': 'provider_documented_unverified',
             'sarvam_tts': 'provider_documented_unverified' if code in TTS else 'unavailable',
             'live_verified': False, 'priority_validation': code in PRIMARY} for code,name in ROWS]


def provider_code(code):
    short = normalize(code)
    return ('od' if short == 'or' else short) + '-IN' if short != 'auto' else 'unknown'
