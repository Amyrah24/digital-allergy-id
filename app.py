import base64
import hashlib
import hmac
import html
import os
import sqlite3
from contextlib import closing
from datetime import date
from io import BytesIO

import streamlit as st
import streamlit.components.v1 as components

# Optional Cloud Storage SDK import
try:
    import firebase_admin
    from firebase_admin import credentials, storage
except ImportError:
    firebase_admin = None

try:
    import qrcode
except ImportError:
    qrcode = None

DB_FILE = "allergy_app.db"
UPLOAD_DIR = "uploaded_verifications"
SEVERITIES = ["Mild", "Moderate", "Severe / Anaphylaxis"]
SEV_CLASS = {"Mild": "mild", "Moderate": "mod", "Severe / Anaphylaxis": "sev"}
SEV_RANK = {"Severe / Anaphylaxis": 0, "Moderate": 1, "Mild": 2}
SEV_SHORT = {"Mild": "Mild", "Moderate": "Moderate", "Severe / Anaphylaxis": "Severe"}

# Medication history constants
MED_STATUSES = ["Active", "Completed", "Discontinued"]
MED_STATUS_CLASS = {"Active": "mod", "Completed": "mild", "Discontinued": "sev"}
NO_ALLERGY = "None"

os.makedirs(UPLOAD_DIR, exist_ok=True)

# ---------------------------------------------------------
# CLOUD STORAGE HELPER FUNCTION
# ---------------------------------------------------------
BASE_APP_URL = "https://digital-allergy-id.streamlit.app"  # Your Streamlit cloud URL


def upload_to_cloud_storage(local_file_path, filename):
    """Uploads local verification file to Cloud Storage (Firebase/S3/Supabase)

    and returns a public access URL.
    """
    simulated_cloud_url = f"{BASE_APP_URL}/view_doc?file={filename}"

    if firebase_admin and os.path.exists("firebase_key.json"):
        try:
            if not firebase_admin._apps:
                cred = credentials.Certificate("firebase_key.json")
                firebase_admin.initialize_app(
                    cred, {"storageBucket": "your-app.appspot.com"}
                )

            bucket = storage.bucket()
            blob = bucket.blob(f"verifications/{filename}")
            blob.upload_from_filename(local_file_path)
            blob.make_public()
            return blob.public_url
        except Exception as err:
            st.warning(f"Cloud upload fallback to local URL: {err}")
            return simulated_cloud_url

    return simulated_cloud_url


# ---------------------------------------------------------
# DATABASE SETUP
# ---------------------------------------------------------
def db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    with closing(db()) as conn, conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL)"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS allergies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                allergen_name TEXT NOT NULL,
                reaction_type TEXT,
                severity TEXT,
                notes TEXT,
                doc_path TEXT NOT NULL,
                cloud_doc_url TEXT,
                FOREIGN KEY (user_id) REFERENCES users (id))"""
        )
        # NEW: medication history module
        conn.execute(
            """CREATE TABLE IF NOT EXISTS medication_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                medication_name TEXT NOT NULL,
                dosage TEXT,
                frequency TEXT,
                start_date TEXT,
                end_date TEXT,
                status TEXT,
                reason TEXT,
                related_allergy TEXT,
                notes TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )"""
        )


def hash_password(password, salt=None):
    salt = salt or os.urandom(16).hex()
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt), 200_000
    ).hex()
    return f"{salt}${digest}"


def verify_password(password, stored):
    if "$" in stored:
        salt = stored.split("$", 1)[0]
        return hmac.compare_digest(hash_password(password, salt), stored)
    legacy = hashlib.sha256(password.encode()).hexdigest()
    return hmac.compare_digest(legacy, stored)


def seed_sample_data():
    with closing(db()) as conn, conn:
        if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
            return
        pwd = hash_password("password123")
        ids = {}
        for email in [
            "990412-14-5231",
            "amir_hafiz@gmail.com",
            "sara_tan98@yahoo.com",
            "lim_wei@utp.edu.my",
        ]:
            cur = conn.execute(
                "INSERT INTO users (email, password_hash) VALUES (?, ?)",
                (email, pwd),
            )
            ids[email] = cur.lastrowid

        demo_file = os.path.join(UPLOAD_DIR, "demo_verification.txt")
        if not os.path.exists(demo_file):
            with open(demo_file, "w") as f:
                f.write(
                    "Official Medical Verification Note - Record Verified by Certified Physician."
                )

        demo_cloud_url = f"{BASE_APP_URL}/view_doc?file=demo_verification.txt"

        rows = [
            (
                "990412-14-5231",
                "Penicillin",
                "Anaphylaxis & facial swelling",
                "Severe / Anaphylaxis",
                "MIMS cross-reactive: Amoxicillin",
                demo_file,
                demo_cloud_url,
            ),
            (
                "990412-14-5231",
                "Ibuprofen (NSAIDs)",
                "Skin rash & hives",
                "Moderate",
                "NPRA reported: avoid Aspirin",
                demo_file,
                demo_cloud_url,
            ),
            (
                "amir_hafiz@gmail.com",
                "Peanuts",
                "Throat tightening",
                "Severe / Anaphylaxis",
                "Patient carries EpiPen",
                demo_file,
                demo_cloud_url,
            ),
        ]
        conn.executemany(
            """INSERT INTO allergies 
               (user_id, allergen_name, reaction_type, severity, notes, doc_path, cloud_doc_url)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            [(ids[e], *rest) for e, *rest in rows],
        )


def seed_sample_medications():
    """Seed demo medication history. Runs separately from seed_sample_data()
    so it also works on an existing database that already has users."""
    with closing(db()) as conn, conn:
        if conn.execute("SELECT COUNT(*) FROM medication_history").fetchone()[0]:
            return
        demo = conn.execute(
            "SELECT id FROM users WHERE email = ?", ("990412-14-5231",)
        ).fetchone()
        if not demo:
            return
        uid = demo["id"]
        rows = [
            (
                uid, "Amoxicillin", "500 mg", "3 times daily",
                "2026-09-01", "2026-09-08", "Discontinued",
                "Severe hives", "Penicillin",
                "Prescribed by Hospital Seri Iskandar",
            ),
            (
                uid, "Ibuprofen", "400 mg", "As needed",
                "2026-05-10", "2026-05-12", "Discontinued",
                "Skin rash", "Ibuprofen (NSAIDs)",
                "Stopped after rash developed",
            ),
            (
                uid, "Paracetamol", "500 mg", "Every 6 hours as needed",
                "2026-10-01", "", "Active",
                "", NO_ALLERGY, "Safe alternative for pain relief",
            ),
        ]
        conn.executemany(
            """INSERT INTO medication_history
               (user_id, medication_name, dosage, frequency, start_date, end_date,
                status, reason, related_allergy, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )


def register_user(email, password):
    try:
        with closing(db()) as conn, conn:
            conn.execute(
                "INSERT INTO users (email, password_hash) VALUES (?, ?)",
                (email.strip().lower(), hash_password(password)),
            )
        return True, "Account created. Sign in to continue."
    except sqlite3.IntegrityError:
        return False, "This Patient ID / email is already registered."


def authenticate_user(email, password):
    with closing(db()) as conn, conn:
        row = conn.execute(
            "SELECT id, email, password_hash FROM users WHERE email = ?",
            (email.strip().lower(),),
        ).fetchone()
        if not row or not verify_password(password, row["password_hash"]):
            return None
        return (row["id"], row["email"])


def get_user_allergies(user_id):
    with closing(db()) as conn:
        rows = conn.execute(
            """SELECT id, allergen_name, reaction_type, severity, notes, doc_path, cloud_doc_url
               FROM allergies WHERE user_id = ?""",
            (user_id,),
        ).fetchall()
    items = [
        {
            "id": r["id"],
            "allergenName": r["allergen_name"],
            "reactionType": r["reaction_type"] or "",
            "severity": r["severity"] or "Mild",
            "notes": r["notes"] or "",
            "docPath": r["doc_path"] or "",
            "cloudDocUrl": r["cloud_doc_url"] or "",
        }
        for r in rows
    ]
    return sorted(
        items, key=lambda a: (SEV_RANK.get(a["severity"], 3), a["allergenName"].lower())
    )


def get_user_by_email_or_ic(patient_query):
    """Retrieve user details, allergies and medications via scanned URL query parameter."""
    with closing(db()) as conn:
        # Search for exact or clean match
        row = conn.execute(
            "SELECT id, email FROM users WHERE REPLACE(email, '-', '') = ? OR email = ?",
            (patient_query, patient_query),
        ).fetchone()
        if not row:
            return None, [], []
        user_id = row["id"]
        email = row["email"]

    allergies = get_user_allergies(user_id)
    medications = get_user_medications(user_id)
    return email, allergies, medications


def add_allergy_record(
    user_id, name, reaction, severity, notes, doc_path, cloud_url
):
    with closing(db()) as conn, conn:
        conn.execute(
            """INSERT INTO allergies 
               (user_id, allergen_name, reaction_type, severity, notes, doc_path, cloud_doc_url)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (user_id, name, reaction, severity, notes, doc_path, cloud_url),
        )


def delete_allergy_record(record_id, user_id, doc_path):
    with closing(db()) as conn, conn:
        conn.execute(
            "DELETE FROM allergies WHERE id = ? AND user_id = ?",
            (record_id, user_id),
        )
    if (
        doc_path
        and os.path.exists(doc_path)
        and "demo_verification.txt" not in doc_path
    ):
        try:
            os.remove(doc_path)
        except OSError:
            pass

def update_medication_status(record_id, user_id, new_status, reason=""):
    """Update the status and optional discontinuation reason of a medication record."""
    with closing(db()) as conn, conn:
        conn.execute(
            """UPDATE medication_history 
               SET status = ?, reason = ? 
               WHERE id = ? AND user_id = ?""",
            (new_status, reason, record_id, user_id),
        )


# ---------------------------------------------------------
# MEDICATION HISTORY FUNCTIONS
# ---------------------------------------------------------
def add_medication_record(
    user_id,
    medication_name,
    dosage,
    frequency,
    start_date,
    end_date,
    status,
    reason,
    related_allergy,
    notes
):
    with closing(db()) as conn, conn:
        conn.execute(
            """INSERT INTO medication_history
            (
                user_id,
                medication_name,
                dosage,
                frequency,
                start_date,
                end_date,
                status,
                reason,
                related_allergy,
                notes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                user_id,
                medication_name,
                dosage,
                frequency,
                start_date,
                end_date,
                status,
                reason,
                related_allergy,
                notes
            )
        )

def get_user_medications(user_id):
    with closing(db()) as conn:
        rows = conn.execute(
            """SELECT
                id,
                medication_name,
                dosage,
                frequency,
                start_date,
                end_date,
                status,
                reason,
                related_allergy,
                notes
            FROM medication_history
            WHERE user_id = ?
            ORDER BY start_date DESC""",
            (user_id,)
        ).fetchall()

    return [dict(row) for row in rows]

def delete_medication_record(record_id, user_id):
    with closing(db()) as conn, conn:
        conn.execute(
            "DELETE FROM medication_history WHERE id = ? AND user_id = ?",
            (record_id, user_id),
        )


def analyze_medication_allergy_relationship(medications, allergies):
    """Link each medication's stored `related_allergy` to the patient's allergy
    records. This is a record-matching step only, NOT a clinical
    cross-reactivity engine."""
    by_name = {a["allergenName"].strip().lower(): a for a in allergies}
    warnings = []
    for med in medications:
        related = (med.get("related_allergy") or "").strip()
        if not related or related == NO_ALLERGY:
            continue
        match = by_name.get(related.lower())
        if match:
            warnings.append(
                {
                    "medication": med["medication_name"],
                    "allergy": match["allergenName"],
                    "severity": match["severity"],
                    "reason": med["reason"] or "",
                    "status": med["status"] or "Unknown",
                }
            )
    warnings.sort(key=lambda w: SEV_RANK.get(w["severity"], 3))
    return warnings


init_db()
seed_sample_data()
seed_sample_medications()

# ---------------------------------------------------------
# STREAMLIT UI CONFIGURATION & STYLING
# ---------------------------------------------------------
st.set_page_config(
    page_title="Digital Allergy ID", page_icon="🛡️", layout="centered"
)

st.markdown(
    """
<style>
@import url('https://fonts.googleapis.com/css2?family=Atkinson+Hyperlegible:ital,wght@0,400;0,700;1,400&display=swap');

:root {
  color-scheme: light;
  --bg: #F2F5F7; --surface: #FFFFFF; --ink: #12222D; --muted: #5B6B77; --line: #D9E1E6; --brand: #12222D;
  --brand-2: #23405A;
  --sev: #C4122F; --sev-2: #E0334E; --sev-bg: #FDECEF;
  --mod: #A85F00; --mod-bg: #FFF3DF;
  --mild: #2D7A5F; --mild-bg: #E6F4EE;
  --shadow-sm: 0 1px 2px rgba(18,34,45,.06), 0 2px 8px rgba(18,34,45,.05);
  --shadow-md: 0 4px 12px rgba(18,34,45,.08), 0 12px 32px rgba(18,34,45,.08);
  --radius: 16px;
}

/* ---------- Base ---------- */
.stApp {
  background:
    radial-gradient(900px 400px at 100% -10%, rgba(196,18,47,.07), transparent 60%),
    radial-gradient(800px 400px at -10% 0%, rgba(35,64,90,.09), transparent 60%),
    var(--bg);
  color: var(--ink);
}
.stApp, .stApp p, .stApp label, .stApp input, .stApp textarea, .stApp button,
.stApp h1, .stApp h2, .stApp h3, .stApp li, .stApp div[data-baseweb] {
  font-family: 'Atkinson Hyperlegible', system-ui, sans-serif;
}
.stApp h1, .stApp h2, .stApp h3, .stApp p, .stApp li, .stApp label { color: var(--ink); }
.stApp h1 { font-weight: 700; letter-spacing: -.02em; }
.stApp h2, .stApp h3 { font-weight: 700; letter-spacing: -.01em; }
.block-container { padding-top: 1.6rem; padding-bottom: 4rem; max-width: 640px; }
#MainMenu, footer, header[data-testid="stHeader"] { visibility: hidden; height: 0; }

/* ---------- Buttons ---------- */

.stButton > button,
.stFormSubmitButton > button,
.stDownloadButton > button {
  background: linear-gradient(135deg, var(--brand), var(--brand-2)) !important;
  color: #FFFFFF !important;
  border: 0 !important;
  border-radius: 12px;
  font-weight: 700;
  min-height: 2.9rem;
  padding: 0 1.2rem;
  box-shadow: 0 2px 6px rgba(18,34,45,.25);
  transition: transform .15s ease, box-shadow .15s ease, filter .15s ease;
}

/* Force the text inside Streamlit buttons to remain white */
.stButton > button *,
.stFormSubmitButton > button *,
.stDownloadButton > button * {
  color: #FFFFFF !important;
}

.stButton > button:hover,
.stFormSubmitButton > button:hover,
.stDownloadButton > button:hover {
  transform: translateY(-1px);
  filter: brightness(1.1);
  box-shadow: 0 6px 16px rgba(18,34,45,.28);
  color: #FFFFFF !important;
}

.stButton > button:active,
.stFormSubmitButton > button:active,
.stDownloadButton > button:active {
  transform: translateY(0);
  box-shadow: 0 1px 3px rgba(18,34,45,.3);
}

.stButton > button:focus-visible,
.stFormSubmitButton > button:focus-visible,
.stDownloadButton > button:focus-visible {
  outline: 3px solid #7FB2E5;
  outline-offset: 2px;
}

/* Disabled buttons */
.stButton > button:disabled,
.stFormSubmitButton > button:disabled,
.stDownloadButton > button:disabled {
  background: linear-gradient(135deg, #23405A, #2D526E) !important;
  color: #FFFFFF !important;
  opacity: 1 !important;
}

.stButton > button:disabled *,
.stFormSubmitButton > button:disabled *,
.stDownloadButton > button:disabled * {
  color: #FFFFFF !important;
}

/* ---------- Form + inputs ---------- */

[data-testid="stForm"] {
  background: var(--surface);
  border: 1.5px solid var(--line);
  border-radius: var(--radius);
  padding: 1.4rem;
  box-shadow: var(--shadow-sm);
}

.stApp label {
  font-weight: 700;
}

/* Input and textarea */
.stApp input,
.stApp textarea {
  border: 1.5px solid #C9D2D8 !important;
    border-radius: 10px !important;
    background: #FFFFFF !important;
    box-shadow: 0 1px 3px rgba(18, 34, 45, 0.06) !important;
}

/* Permanent soft grey border */
.stApp div[data-baseweb="input"],
.stApp div[data-baseweb="textarea"] {
  background: #FFFFFF !important;
  border: 1.5px solid #6B7C88 !important;
  border-radius: 10px !important;
  box-shadow: none !important;
  transition: border-color .15s ease;
}

/* Dropdown/select */
.stApp div[data-baseweb="select"] > div {
  background: #FFFFFF !important;
  border: 1.5px solid #C9D2D8 !important;
  border-radius: 10px !important;
  box-shadow: none !important;
}

/* When clicked: remain grey, only slightly darker */
.stApp div[data-baseweb="input"]:focus-within,
.stApp div[data-baseweb="textarea"]:focus-within,
.stApp div[data-baseweb="select"] > div:focus-within {
  border-color: #AEBBC4 !important;
  box-shadow: none !important;
}

/* Placeholder */
.stApp input::placeholder,
.stApp textarea::placeholder {
  color: #6B7C88 !important;
  opacity: 1;
}

/* ---------- Allergy cards ---------- */
.al-card {
  position: relative; background: var(--surface);
  border: 1.5px solid var(--line); border-left-width: 7px; border-radius: 14px;
  padding: 1rem 1.2rem; margin-top: .8rem; box-shadow: var(--shadow-sm);
  transition: transform .15s ease, box-shadow .15s ease;
}
.al-card:hover { transform: translateY(-2px); box-shadow: var(--shadow-md); }
.al-card.sev  { border-left-color: var(--sev);  background: linear-gradient(90deg, var(--sev-bg) 0%, var(--surface) 38%); }
.al-card.mod  { border-left-color: var(--mod);  background: linear-gradient(90deg, var(--mod-bg) 0%, var(--surface) 38%); }
.al-card.mild { border-left-color: var(--mild); background: linear-gradient(90deg, var(--mild-bg) 0%, var(--surface) 38%); }
.al-top { display: flex; justify-content: space-between; align-items: center; gap: .6rem; }
.al-name { font-size: 1.25rem; font-weight: 700; letter-spacing: -.01em; }

/* ---------- Medication rows ---------- */
.med-row { display: flex; gap: .5rem; margin-top: .35rem; font-size: .95rem; }
.med-row b { min-width: 7.5rem; color: var(--muted); font-weight: 700; }
.med-row span { flex: 1; word-break: break-word; }

/* ---------- Medication-allergy relationship ---------- */
.rel-card {
  background: var(--surface); border: 1.5px solid var(--sev);
  border-radius: 14px; padding: 1rem 1.2rem; margin-top: .8rem;
  box-shadow: var(--shadow-sm);
}
.rel-title { font-weight: 700; color: var(--sev); margin-bottom: .6rem; }
.rel-chain { display: flex; flex-wrap: wrap; align-items: center; gap: .4rem; }
.rel-step {
  background: var(--bg); border: 1.5px solid var(--line); border-radius: 10px;
  padding: .25rem .65rem; font-size: .9rem; font-weight: 700;
}
.rel-arrow { color: var(--muted); font-weight: 700; }

/* ---------- Badges (status dot so meaning isn't color-only) ---------- */
.badge {
  display: inline-flex; align-items: center; gap: .4rem;
  font-size: .85rem; font-weight: 700; padding: .2rem .75rem; border-radius: 999px; white-space: nowrap;
}
.badge::before { content: ""; width: .5rem; height: .5rem; border-radius: 50%; background: currentColor; }
.badge.sev  { background: var(--sev-bg);  color: var(--sev); }
.badge.mod  { background: var(--mod-bg);  color: var(--mod); }
.badge.mild { background: var(--mild-bg); color: var(--mild); }

/* ---------- SOS banner ---------- */
.sos {
  position: relative; overflow: hidden; color: #fff;
  background: linear-gradient(135deg, var(--sev), var(--sev-2));
  border-radius: var(--radius); padding: 1.2rem 1.4rem; margin: .4rem 0 1rem;
  box-shadow: 0 8px 24px rgba(196,18,47,.35);
}
.sos, .sos * { color: #fff; }
.sos::after {
  content: ""; position: absolute; right: -40px; top: -40px; width: 140px; height: 140px;
  border-radius: 50%; background: rgba(255,255,255,.12);
  animation: sos-pulse 2.4s ease-out infinite;
}
@keyframes sos-pulse {
  0%   { transform: scale(.8); opacity: .8; }
  100% { transform: scale(1.6); opacity: 0; }
}

/* ---------- Medical ID card ---------- */
.id-card {
  background: var(--surface); border: 2px solid var(--ink);
  border-radius: 18px; overflow: hidden; box-shadow: var(--shadow-md);
}
.id-band {
  background: linear-gradient(135deg, var(--sev), var(--sev-2)); color: #fff;
  padding: .8rem 1.2rem; font-weight: 700; font-size: 1.1rem; letter-spacing: .02em;
  display: flex; align-items: center; gap: .6rem;
}
.id-body { padding: 1.2rem 1.3rem 1.4rem; display: grid; gap: 1.1rem; }
.id-who small {
  color: var(--muted); display: block; font-size: .75rem;
  text-transform: uppercase; letter-spacing: .08em; margin-bottom: .15rem;
}
.id-who b { font-size: 1.2rem; word-break: break-all; }
.id-list { display: flex; flex-wrap: wrap; gap: .45rem; }
.id-list > * {
  background: var(--sev-bg); color: var(--sev); font-weight: 700;
  padding: .25rem .7rem; border-radius: 999px; font-size: .9rem;
}
.id-qr { text-align: center; padding-top: 1rem; border-top: 1.5px dashed var(--line); }
.id-qr img {
  width: 220px; max-width: 100%; image-rendering: pixelated;
  padding: .6rem; background: #fff; border: 1.5px solid var(--line); border-radius: 14px;
}

/* ---------- Accessibility + print ---------- */
@media (prefers-reduced-motion: reduce) {
  .sos::after { animation: none; }
  .al-card, .stButton > button { transition: none; }
}
@media print {
  .stApp { background: #fff; }
  .id-card { box-shadow: none; break-inside: avoid; }
  .stButton, .stDownloadButton, [data-testid="stForm"] { display: none; }
}
</style>
""",
    unsafe_allow_html=True,
)

e = html.escape


def brand_header(subtitle):
    return f"""
    <div class="brand" style="display:flex; align-items:center; gap:.7rem;">
      <div style="width:2.6rem; height:2.6rem; border-radius:12px; background:var(--brand); display:grid; place-items:center; font-size:1.3rem;">🛡️</div>
      <div>
        <div style="font-size:1.35rem; font-weight:700; line-height:1.1;">Digital Allergy ID</div>
        <div style="color:var(--muted); font-size:.9rem;">{e(subtitle)}</div>
      </div>
    </div>"""


def flash(msg):
    st.session_state["flash"] = msg


def med_card_html(med):
    """Render one medication history card."""
    status = med["status"] or "Unknown"
    cls = MED_STATUS_CLASS.get(status, "mild")
    start = med["start_date"] or "-"
    end = med["end_date"] or ("Ongoing" if status == "Active" else "-")

    def row(label, value):
        return f'<div class="med-row"><b>{label}</b><span>{e(value or "-")}</span></div>'

    related = med["related_allergy"]
    related = "" if related == NO_ALLERGY else related
    return f"""<div class="al-card {cls}">
      <div class="al-top">
        <span class="al-name">{e(med["medication_name"])}</span>
        <span class="badge {cls}">{e(status)}</span>
      </div>
      {row("Dosage", med["dosage"])}
      {row("Frequency", med["frequency"])}
      {row("Period", f"{start} → {end}")}
      {row("Reason stopped", med["reason"])}
      {row("Related allergy", related)}
      {row("Notes", med["notes"])}
    </div>"""


def relationship_html(w):
    """Render the Medication → Status → Reason → Allergy → Severity chain."""
    steps = [
        w["medication"],
        w["status"],
        w["reason"] or "No reason recorded",
        f"{w['allergy']} allergy",
        SEV_SHORT.get(w["severity"], w["severity"]),
    ]
    chain = '<span class="rel-arrow">→</span>'.join(
        f'<span class="rel-step">{e(s)}</span>' for s in steps
    )
    return f"""<div class="rel-card">
      <div class="rel-title">⚠️ Medication–Allergy Relationship Identified</div>
      <div class="rel-chain">{chain}</div>
    </div>"""


# Initialize session state keys
if "user" not in st.session_state:
    st.session_state.user = None
if "sos" not in st.session_state:
    st.session_state.sos = False
if st.session_state.get("flash"):
    st.toast(st.session_state.pop("flash"), icon="✅")

# ---------------------------------------------------------
# PUBLIC VERIFICATION VIEWER (SCANNED QR & PUBLIC DOC LANDING)
# ---------------------------------------------------------
query_params = st.query_params

# Public Document Handler
if "file" in query_params:
    requested_file = query_params["file"]
    file_path = os.path.join(UPLOAD_DIR, requested_file)
    abs_path = os.path.abspath(file_path)

    st.markdown(
        brand_header("Public Medical Document Inspector"),
        unsafe_allow_html=True,
    )
    st.write("")

    if os.path.exists(abs_path):
        ext = abs_path.split(".")[-1].lower()
        if ext in ["png", "jpg", "jpeg"]:
            st.image(
                abs_path,
                caption=f"Verification Document ({requested_file})",
                use_container_width=True,
            )
        elif ext == "pdf":
            with open(abs_path, "rb") as f:
                pdf_bytes = f.read()
                pdf_b64 = base64.b64encode(pdf_bytes).decode("utf-8")

            pdf_html = f"""
            <iframe src="https://mozilla.github.io/pdf.js/web/viewer.html?file=data:application/pdf;base64,{pdf_b64}" 
                    width="100%" height="550px" style="border:none;">
            </iframe>
            """
            components.html(pdf_html, height=560)
            st.download_button(
                "📥 Download Document PDF",
                data=pdf_bytes,
                file_name=requested_file,
                mime="application/pdf",
            )
    else:
        st.error(f"❌ Requested document (`{requested_file}`) was not found on the server.")

    st.divider()
    if st.button("← Back to Sign In Portal", use_container_width=True):
        st.query_params.clear()
        st.rerun()

    st.stop()

# ---------------------------------------------------------
# AUTHENTICATION
# ---------------------------------------------------------
if st.session_state.user is None:
    st.markdown(
        brand_header(
            "Your allergies, ready for any pharmacist or first responder"
        ),
        unsafe_allow_html=True,
    )
    st.write("")

    tab_in, tab_reg = st.tabs(["Sign in", "Create account"])

    with tab_in:
        with st.form("login_form"):
            email_in = st.text_input(
                "Patient ID or email",
                placeholder="990412-14-5231 or you@mail.com",
            )
            pass_in = st.text_input("Password", type="password")
            if st.form_submit_button("Sign in", use_container_width=True):
                if not (email_in and pass_in):
                    st.warning(
                        "Enter your Patient ID or email and your password."
                    )
                else:
                    user = authenticate_user(email_in, pass_in)
                    if user:
                        st.session_state.user = user
                        st.rerun()
                    else:
                        st.error("Patient ID / email or password is incorrect.")
        st.caption("Demo account: 990412-14-5231 · password123")

    with tab_reg:
        with st.form("register_form"):
            reg_email = st.text_input(
                "Patient ID or email", placeholder="990412-14-5231"
            )
            reg_pass = st.text_input(
                "Password (at least 8 characters)", type="password"
            )
            reg_pass2 = st.text_input("Confirm password", type="password")
            if st.form_submit_button("Create account", use_container_width=True):
                if not (reg_email.strip() and reg_pass):
                    st.warning("Enter a Patient ID or email and a password.")
                elif len(reg_pass) < 8:
                    st.error("Password must be at least 8 characters.")
                elif reg_pass != reg_pass2:
                    st.error("Passwords do not match.")
                else:
                    ok, msg = register_user(reg_email, reg_pass)
                    (st.success if ok else st.error)(msg)

# ---------------------------------------------------------
# DASHBOARD (LOGGED IN)
# ---------------------------------------------------------
else:
    user_id, user_email = st.session_state.user
    allergies = get_user_allergies(user_id)
    medications = get_user_medications(user_id)

    head, out = st.columns([4, 1], vertical_alignment="center")
    with head:
        st.markdown(brand_header("Patient portal"), unsafe_allow_html=True)
    with out:
        if st.button("Log out", use_container_width=True):
            st.session_state.user = None
            st.session_state.sos = False
            st.rerun()

    tab_dash, tab_med, tab_add, tab_qr = st.tabs(
        ["My allergies", "Medication history", "Add allergy", "Emergency card"]
    )

    # ---- My allergies ----
    with tab_dash:
        c_search, c_sos = st.columns([3, 2], vertical_alignment="bottom")
        with c_search:
            search = st.text_input(
                "Search allergies",
                placeholder="Search by allergen or reaction",
                label_visibility="collapsed",
            )
        with c_sos:
            if st.button("🚨 Show SOS alert", use_container_width=True):
                st.session_state.sos = True

        if st.session_state.sos:
            severe = [
                a["allergenName"]
                for a in allergies
                if a["severity"] == "Severe / Anaphylaxis"
            ]
            st.markdown(
                f"""<div class="sos">
                  <h3>Critical allergy alert</h3>
                  <p>Patient: <b>{e(user_email)}</b></p>
                  <div>{e(', '.join(severe)) if severe else 'No severe allergens recorded'}</div>
                </div>""",
                unsafe_allow_html=True,
            )
            if st.button("Close alert"):
                st.session_state.sos = False
                st.rerun()

        q = search.strip().lower()
        shown = [
            a
            for a in allergies
            if q in a["allergenName"].lower() or q in a["reactionType"].lower()
        ]

        if not allergies:
            st.info("No allergies recorded yet. Open the Add allergy tab to add your first one.")
        elif not shown:
            st.info(f'No allergies match "{search}". Clear the search to see all records.')
        else:
            for a in shown:
                cls = SEV_CLASS.get(a["severity"], "mild")
                st.markdown(
                    f"""<div class="al-card {cls}">
                      <div class="al-top"><span class="al-name">{e(a['allergenName'])}</span>
                      <span class="badge {cls}">{SEV_SHORT.get(a['severity'], a['severity'])}</span></div>
                      {f'<div class="al-rx">{e(a["reactionType"])}</div>' if a['reactionType'] else ''}
                      {f'<div class="al-note">{e(a["notes"])}</div>' if a['notes'] else ''}
                    </div>""",
                    unsafe_allow_html=True,
                )

                if a["docPath"]:
                    abs_doc_path = os.path.abspath(a["docPath"])
                    if os.path.exists(abs_doc_path):
                        ext = abs_doc_path.split(".")[-1].lower()
                        with st.expander(f"📄 View Doctor's Verification ({a['allergenName']})"):
                            if ext in ["png", "jpg", "jpeg"]:
                                st.image(
                                    abs_doc_path,
                                    caption=f"Doctor Verification for {a['allergenName']}",
                                    use_container_width=True,
                                )
                            elif ext == "pdf":
                                with open(abs_doc_path, "rb") as f:
                                    pdf_bytes = f.read()
                                st.download_button(
                                    label=f"📥 Download {a['allergenName']} PDF",
                                    data=pdf_bytes,
                                    file_name=f"Verification_{a['allergenName']}.pdf",
                                    mime="application/pdf",
                                    key=f"dl_pdf_{a['id']}",
                                )
                            else:
                                with open(abs_doc_path, "r", errors="ignore") as f:
                                    st.info(f.read())
                    else:
                        st.caption(f"⚠️ Document file missing on disk (`{a['docPath']}`)")

                with st.popover("Remove record"):
                    st.write(f"Remove **{a['allergenName']}** and its verification file?")
                    if st.button("Yes, remove", key=f"del_{a['id']}"):
                        delete_allergy_record(a["id"], user_id, a["docPath"])
                        flash(f"Removed {a['allergenName']}")
                        st.rerun()

    # ---- Medication history ----
    with tab_med:
        st.subheader("Medication History")

        warnings = analyze_medication_allergy_relationship(medications, allergies)
        for w in warnings:
            st.markdown(relationship_html(w), unsafe_allow_html=True)

        if not medications:
            st.info("No medication history recorded yet.")
        else:
            for med in medications:
                st.markdown(med_card_html(med), unsafe_allow_html=True)

                c_edit, c_del = st.columns([3, 1])

                with c_edit:
                    with st.popover(f"✏️ Change status: {med['medication_name']}"):
                        with st.form(f"edit_status_form_{med['id']}"):
                            new_status = st.selectbox(
                                "New Status",
                                MED_STATUSES,
                                index=MED_STATUSES.index(med["status"])
                                if med["status"] in MED_STATUSES
                                else 0,
                                key=f"status_sel_{med['id']}",
                            )
                            new_reason = st.text_input(
                                "Reason for change / discontinuation",
                                value=med.get("reason", "") or "",
                                placeholder="e.g. Completed full course / Severe rash developed",
                                key=f"reason_in_{med['id']}",
                            )
                            if st.form_submit_button("Update Status", use_container_width=True):
                                update_medication_status(
                                    med["id"], user_id, new_status, new_reason.strip()
                                )
                                flash(f"Updated {med['medication_name']} to {new_status}")
                                st.rerun()

                with c_del:
                    with st.popover("🗑️ Delete"):
                        st.write(f"Remove **{med['medication_name']}** from history?")
                        if st.button(
                            "Yes, delete",
                            key=f"del_med_btn_{med['id']}",
                            use_container_width=True,
                        ):
                            delete_medication_record(med["id"], user_id)
                            flash(f"Removed {med['medication_name']}")
                            st.rerun()

                st.write("")

        st.divider()
        st.subheader("Add Medication Record")

        with st.form("add_medication_form"):
            medication_name = st.text_input("Medication name *", placeholder="e.g. Amoxicillin")
            dosage = st.text_input("Dosage", placeholder="e.g. 500 mg")
            frequency = st.text_input("Frequency", placeholder="e.g. 3 times daily")

            col1, col2 = st.columns(2)
            with col1:
                start_date = st.date_input("Start date")
            with col2:
                end_date = st.date_input("End date")

            status = st.selectbox("Status", MED_STATUSES)
            reason = st.text_input("Reason", placeholder="e.g. Severe hives")
            related_allergy = st.selectbox(
                "Related allergy",
                [NO_ALLERGY] + [a["allergenName"] for a in allergies],
            )
            notes = st.text_area("Notes", placeholder="Additional information")

            if st.form_submit_button("Save Medication Record", use_container_width=True):
                if not medication_name.strip():
                    st.error("Please enter the medication name.")
                else:
                    add_medication_record(
                        user_id,
                        medication_name.strip(),
                        dosage.strip(),
                        frequency.strip(),
                        str(start_date),
                        "" if status == "Active" else str(end_date),
                        status,
                        reason.strip(),
                        related_allergy,
                        notes.strip(),
                    )
                    flash(f"Saved medication record for {medication_name.strip()}")
                    st.rerun()

    # ---- Add allergy ----
    with tab_add:
        st.write("⚠️ **Medical Verification Required:** Attach supporting document from a doctor.")
        with st.form("add_allergy_form", clear_on_submit=True):
            name = st.text_input(
                "Allergen or drug name*", placeholder="Penicillin, aspirin, peanuts"
            )
            reaction = st.text_input(
                "Reaction", placeholder="Hives, swelling, trouble breathing"
            )
            severity = st.radio("Severity", SEVERITIES, horizontal=True)
            allergy_notes = st.text_area(
                "Notes or hospital reference (optional)",
                placeholder="Verified by Hospital Seri Iskandar",
            )
            uploaded_doc = st.file_uploader(
                "Attach Doctor's Supporting Document (PDF, PNG, JPG)*",
                type=["pdf", "png", "jpg", "jpeg"],
                help="Attach proof or signature from a certified medical practitioner.",
            )

            if st.form_submit_button("Save verified allergy", use_container_width=True):
                if not name.strip():
                    st.error("Enter the allergen or drug name.")
                elif uploaded_doc is None:
                    st.error("❌ You must upload a supporting doctor's verification document.")
                else:
                    f_ext = uploaded_doc.name.split(".")[-1]
                    saved_filename = f"user_{user_id}_{name.strip().replace(' ', '_')}.{f_ext}"
                    file_path = os.path.join(UPLOAD_DIR, saved_filename)
                    with open(file_path, "wb") as f:
                        f.write(uploaded_doc.getbuffer())

                    cloud_url = upload_to_cloud_storage(file_path, saved_filename)
                    add_allergy_record(
                        user_id,
                        name.strip(),
                        reaction.strip(),
                        severity,
                        allergy_notes.strip() or "Verified by Doctor",
                        file_path,
                        cloud_url,
                    )
                    flash(f"Saved verified record for {name.strip()}")
                    st.rerun()

    # ---- Emergency card ----
    with tab_qr:
        st.write(
            "Show this card to a pharmacist or first responder. When scanned, it opens your verified profile directly."
        )

        # Clean NRIC / Patient ID for URL parameter
        clean_patient_id = user_email.replace("-", "").strip()
        payload = f"{BASE_APP_URL}/?patient={clean_patient_id}"

        # Backup API URL if local qrcode library import fails
        qr_api_url = f"https://api.qrserver.com/v1/create-qr-code/?size=220x220&data={payload}"

        chips = "".join(
            f'<span class="badge {SEV_CLASS.get(a["severity"], "mild")}">{e(a["allergenName"])}</span>'
            for a in allergies
        ) or '<span class="al-note">No allergies recorded</span>'

        st.markdown(
            f"""<div class="id-card">
              <div class="id-band">Medical alert: verified cloud allergies</div>
              <div class="id-body">
                <div class="id-who"><small>Patient ID</small><b>{e(user_email)}</b></div>
                <div class="id-list">{chips}</div>
                <div class="id-qr"><img src="{qr_api_url}" alt="QR code with verification URL"></div>
              </div>
            </div>""",
            unsafe_allow_html=True,
        )

        with st.expander("Inspect Raw QR Code Payload"):
            st.code(payload, language="text")