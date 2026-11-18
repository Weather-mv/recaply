"""
PythonAnywhere WSGI file for Recaply's online service (cloud.py).

Paste this whole file into the WSGI configuration file linked on the Web tab
(/var/www/<username>_pythonanywhere_com_wsgi.py), replacing what's there.

    ~/recaply        the code (unzipped recaply-cloud.zip)
    ~/recaply_data   accounts database and secret key (kept when the code is replaced)

Secrets (Stripe keys) go in ~/recaply/.env, never in this file.
"""

import getpass
import os
import sys

HOME = os.path.expanduser("~")
CODE = os.path.join(HOME, "recaply")
SITE = f"https://{getpass.getuser().lower()}.pythonanywhere.com"

os.environ.setdefault("RECAPLY_DATA_DIR", os.path.join(HOME, "recaply_data"))
os.environ.setdefault("RECAPLY_PUBLIC_URL", SITE)
os.environ.setdefault("RECAPLY_SECURE_COOKIES", "1")     # the site is HTTPS only (Force HTTPS on the Web tab)
os.environ.setdefault("RECAPLY_TRUST_PROXY", "1")        # real visitor address for the login rate limits
# Not 127.0.0.1: on a public server plans must never switch without a payment.
os.environ.setdefault("HOST", "0.0.0.0")
os.environ.setdefault("RECAPLY_DESKTOP", "0")

if CODE not in sys.path:
    sys.path.insert(0, CODE)
os.chdir(CODE)

# config.py reads ~/recaply/.env (optional: Stripe keys, admin emails) when cloud is imported.
from cloud import app as application  # noqa: E402,F401
