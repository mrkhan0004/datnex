import os, io, re, secrets, sqlite3, hashlib
from datetime import datetime
from functools import wraps
from flask import (Flask, render_template, request, redirect, url_for,
                   session, flash, send_file, g, abort, has_app_context, jsonify)
from werkzeug.security import generate_password_hash, check_password_hash
import pandas as pd
import qrcode

# ================= CONFIG =================
BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, "database.db")
for d in ("uploads", "exports", os.path.join("static", "qr")):
    os.makedirs(os.path.join(BASE, d), exist_ok=True)

UPI_ID_DEFAULT      = "7648990004@rpl"      # Fallback defaults
UPI_NAME_DEFAULT    = "Mohan_Yadav"
WHATSAPP_NUMBER_DEFAULT  = "917648990004"
WHATSAPP_DISPLAY_DEFAULT = "+91 76489 90004"
WHATSAPP_MSG_DEFAULT     = "Hello datnex, I am interested in GST Business Database packages."
ADMIN_USER  = "admin"
ADMIN_PASS  = "admin@123"         # <<< pehli login ke baad change karna
AUTO_VERIFY = False               # True = UTR dalte hi download unlock (testing ke liye)
MAX_DOWNLOADS = 5

PRICES = {"pan_india": 5999, "state": 999, "city": 599}
CATEGORY_ADDON = 499
GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")

app = Flask(__name__)
app.secret_key = "ZFyX4kqR74kgBDXpyOqLexIiX3VQBOi/T+RRGZeqi+M="   # <<< change karna

# ================= SETTINGS HELPERS =================
def get_setting(key, default=None):
    try:
        if has_app_context():
            db = get_db()
            close_conn = False
        else:
            db = sqlite3.connect(DB_PATH)
            db.row_factory = sqlite3.Row
            close_conn = True
        r = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        val = r["value"] if (r and r["value"] is not None) else default
        if close_conn: db.close()
        return val
    except Exception:
        return default

def set_setting(key, value):
    if has_app_context():
        db = get_db()
        close_conn = False
    else:
        db = sqlite3.connect(DB_PATH)
        db.row_factory = sqlite3.Row
        close_conn = True
    db.execute("INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
               (key, str(value) if value is not None else ""))
    db.commit()
    if close_conn: db.close()

def get_all_settings():
    defaults = {
        "upi_id": UPI_ID_DEFAULT,
        "upi_name": UPI_NAME_DEFAULT,
        "call_number": "+917648990004",
        "call_display": "+91 76489 90004",
        "whatsapp_number": WHATSAPP_NUMBER_DEFAULT,
        "whatsapp_display": WHATSAPP_DISPLAY_DEFAULT,
        "whatsapp_default_msg": WHATSAPP_MSG_DEFAULT,
        "qr_mode": "dynamic",
        "custom_qr_file": "",
        "smtp_host": "smtp.gmail.com",
        "smtp_port": "587",
        "smtp_user": "",
        "smtp_pass": "",
        "smtp_from": ""
    }
    try:
        if has_app_context():
            db = get_db()
            close_conn = False
        else:
            db = sqlite3.connect(DB_PATH)
            db.row_factory = sqlite3.Row
            close_conn = True
        rows = db.execute("SELECT key, value FROM settings").fetchall()
        for r in rows:
            defaults[r["key"]] = r["value"]
        if close_conn: db.close()
    except Exception:
        pass
    return defaults

@app.context_processor
def inject_globals():
    import urllib.parse
    cfg = get_all_settings()
    wa_num = cfg.get("whatsapp_number", WHATSAPP_NUMBER_DEFAULT)
    wa_disp = cfg.get("whatsapp_display", WHATSAPP_DISPLAY_DEFAULT)
    wa_msg = cfg.get("whatsapp_default_msg", WHATSAPP_MSG_DEFAULT)
    call_num = cfg.get("call_number", "+917648990004")
    call_disp = cfg.get("call_display", "+91 76489 90004")
    upi = cfg.get("upi_id", UPI_ID_DEFAULT)
    upi_nm = cfg.get("upi_name", UPI_NAME_DEFAULT)

    clean_wa = re.sub(r"[^\d]", "", str(wa_num))
    clean_call = re.sub(r"[^\d+]", "", str(call_num))
    if clean_call and not clean_call.startswith("+") and len(clean_call) == 10:
        clean_call = "+91" + clean_call

    def whatsapp_url(msg=None):
        m = msg or wa_msg
        return f"https://wa.me/{clean_wa}?text={urllib.parse.quote(m)}"

    return {
        "whatsapp_number": clean_wa,
        "whatsapp_redirect": clean_wa,
        "whatsapp_display": wa_disp,
        "whatsapp_url": whatsapp_url,
        "whatsapp_tel": clean_call,
        "call_number": clean_call,
        "call_display": call_disp,
        "upi": upi,
        "upi_name": upi_nm,
        "settings": cfg,
    }


# ================= SCHEMA =================
SCHEMA = """
CREATE TABLE IF NOT EXISTS settings(
  key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS admin_users(
  id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS states(
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL,
  slug TEXT UNIQUE NOT NULL, total_leads INTEGER DEFAULT 0,
  price INTEGER DEFAULT 999);
CREATE TABLE IF NOT EXISTS cities(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  state_id INTEGER NOT NULL REFERENCES states(id) ON DELETE CASCADE,
  name TEXT NOT NULL, slug TEXT NOT NULL, total_leads INTEGER DEFAULT 0,
  price INTEGER DEFAULT 599, UNIQUE(state_id,name));
CREATE TABLE IF NOT EXISTS categories(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT UNIQUE NOT NULL, slug TEXT UNIQUE NOT NULL);
CREATE TABLE IF NOT EXISTS leads(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  company_name TEXT, address TEXT,
  state_id INTEGER REFERENCES states(id) ON DELETE SET NULL,
  city_id INTEGER REFERENCES cities(id) ON DELETE SET NULL,
  category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL,
  email TEXT, phone1 TEXT, phone2 TEXT, phone3 TEXT,
  owner_name TEXT, gstin TEXT UNIQUE,
  created_at TEXT DEFAULT (datetime("now")));
CREATE INDEX IF NOT EXISTS idx_leads_state ON leads(state_id);
CREATE INDEX IF NOT EXISTS idx_leads_city  ON leads(city_id);
CREATE INDEX IF NOT EXISTS idx_leads_cat   ON leads(category_id);
CREATE TABLE IF NOT EXISTS orders(
  id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT UNIQUE NOT NULL,
  package_type TEXT NOT NULL, state_id INTEGER, city_id INTEGER,
  category_id INTEGER, amount INTEGER NOT NULL,
  buyer_name TEXT, buyer_email TEXT, buyer_phone TEXT, utr_number TEXT,
  status TEXT DEFAULT "pending", download_token TEXT UNIQUE,
  download_count INTEGER DEFAULT 0,
  created_at TEXT DEFAULT (datetime("now")));
CREATE TABLE IF NOT EXISTS uploads_log(
  id INTEGER PRIMARY KEY AUTOINCREMENT, filename TEXT, total_rows INTEGER,
  inserted INTEGER, skipped INTEGER,
  uploaded_at TEXT DEFAULT (datetime("now")));
CREATE TABLE IF NOT EXISTS blog_posts(
  id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, slug TEXT UNIQUE,
  excerpt TEXT, body TEXT, created_at TEXT DEFAULT (datetime("now")));
CREATE TABLE IF NOT EXISTS sample_requests(
  id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT, phone TEXT, ip TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
"""

def slugify(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db

@app.teardown_appcontext
def close_db(e):
    db = g.pop("db", None)
    if db: db.close()

LOCATIONS = [
  {"state": "Andaman and Nicobar Islands", "districts": ["Nicobars", "North And Middle Andaman", "South Andamans"]},
  {"state": "Andhra Pradesh", "districts": ["Alluri Sitharama Raju", "Anakapalli", "Ananthapuramu", "Annamayya", "Bapatla", "Chittoor", "Dr. B.R. Ambedkar Konaseema", "East Godavari", "Eluru", "Guntur", "Kakinada", "Krishna", "Kurnool", "Markapuram", "Nandyal", "Ntr", "Palnadu", "Parvathipuram Manyam", "Polavaram", "Prakasam", "Sri Potti Sriramulu Nellore", "Sri Sathya Sai", "Srikakulam", "Tirupati", "Visakhapatnam", "Vizianagaram", "West Godavari", "Y.S.R. Kadapa"]},
  {"state": "Arunachal Pradesh", "districts": ["Anjaw", "Bichom", "Changlang", "Dibang Valley", "East Kameng", "East Siang", "Kamle", "Keyi Panyor", "Kra Daadi", "Kurung Kumey", "Leparada", "Lohit", "Longding", "Lower Dibang Valley", "Lower Siang", "Lower Subansiri", "Namsai", "Pakke Kessang", "Papum Pare", "Shi Yomi", "Siang", "Tawang", "Tirap", "Upper Siang", "Upper Subansiri", "West Kameng", "West Siang"]},
  {"state": "Assam", "districts": ["Bajali", "Baksa", "Barpeta", "Biswanath", "Bongaigaon", "Cachar", "Charaideo", "Chirang", "Darrang", "Dhemaji", "Dhubri", "Dibrugarh", "Dima Hasao", "Goalpara", "Golaghat", "Hailakandi", "Hojai", "Jorhat", "Kamrup", "Kamrup Metro", "Karbi Anglong", "Kokrajhar", "Lakhimpur", "Majuli", "Marigaon", "Nagaon", "Nalbari", "Sivasagar", "Sonitpur", "South Salmara Mancachar", "Sribhumi", "Tamulpur", "Tinsukia", "Udalguri", "West Karbi Anglong"]},
  {"state": "Chandigarh", "districts": ["Chandigarh"]},
  {"state": "Dadra and Nagar Haveli and Daman and Diu", "districts": ["Dadra And Nagar Haveli", "Daman", "Diu"]},
  {"state": "Delhi", "districts": ["Central", "Central North", "East", "New Delhi", "North", "North East", "North West", "Old Delhi", "Outer North", "South", "South East", "South West", "West"]},
  {"state": "Goa", "districts": ["Kushavati", "North Goa", "South Goa"]},
  {"state": "Bihar", "districts": ["Araria", "Arwal", "Aurangabad", "Banka", "Begusarai", "Bhagalpur", "Bhojpur", "Buxar", "Darbhanga", "Gaya", "Gopalganj", "Jamui", "Jehanabad", "Kaimur (Bhabua)", "Katihar", "Khagaria", "Kishanganj", "Lakhisarai", "Madhepura", "Madhubani", "Munger", "Muzaffarpur", "Nalanda", "Nawada", "Pashchim Champaran", "Patna", "Purbi Champaran", "Purnia", "Rohtas", "Saharsa", "Samastipur", "Saran", "Sheikhpura", "Sheohar", "Sitamarhi", "Siwan", "Supaul", "Vaishali"]},
  {"state": "Chhattisgarh", "districts": ["Balod", "Balodabazar-Bhatapara", "Balrampur-Ramanujganj", "Bastar", "Bemetara", "Bijapur", "Bilaspur", "Dakshin Bastar Dantewada", "Dhamtari", "Durg", "Gariyaband", "Gaurela-Pendra-Marwahi", "Janjgir-Champa", "Jashpur", "Kabeerdham", "Khairagarh-Chhuikhadan-Gandai", "Kondagaon", "Korba", "Korea", "Mahasamund", "Manendragarh-Chirmiri-Bharatpur(M C B)", "Mohla-Manpur-Ambagarh Chouki", "Mungeli", "Narayanpur", "Raigarh", "Raipur", "Rajnandgaon", "Sakti", "Sarangarh-Bilaigarh", "Sukma", "Surajpur", "Surguja", "Uttar Bastar Kanker"]},
  {"state": "Haryana", "districts": ["Ambala", "Bhiwani", "Charkhi Dadri", "Faridabad", "Fatehabad", "Gurugram", "Hansi", "Hisar", "Jhajjar", "Jind", "Kaithal", "Karnal", "Kurukshetra", "Mahendragarh", "Nuh", "Palwal", "Panchkula", "Panipat", "Rewari", "Rohtak", "Sirsa", "Sonipat", "Yamunanagar"]},
  {"state": "Jammu and Kashmir", "districts": ["Anantnag", "Bandipora", "Baramulla", "Budgam", "Doda", "Ganderbal", "Jammu", "Kathua", "Kishtwar", "Kulgam", "Kupwara", "Poonch", "Pulwama", "Rajouri", "Ramban", "Reasi", "Samba", "Shopian", "Srinagar", "Udhampur"]},
  {"state": "Gujarat", "districts": ["Ahmedabad", "Amreli", "Anand", "Arvalli", "Banas Kantha", "Bharuch", "Bhavnagar", "Botad", "Chhotaudepur", "Dahod", "Dangs", "Devbhumi Dwarka", "Gandhinagar", "Gir Somnath", "Jamnagar", "Junagadh", "Kachchh", "Kheda", "Mahesana", "Mahisagar", "Morbi", "Narmada", "Navsari", "Panch Mahals", "Patan", "Porbandar", "Rajkot", "Sabar Kantha", "Surat", "Surendranagar", "Tapi", "Vadodara", "Valsad", "Vav-Tharad"]},
  {"state": "Jharkhand", "districts": ["Bokaro", "Chatra", "Deoghar", "Dhanbad", "Dumka", "East Singhbum", "Garhwa", "Giridih", "Godda", "Gumla", "Hazaribagh", "Jamtara", "Khunti", "Koderma", "Latehar", "Lohardaga", "Pakur", "Palamu", "Ramgarh", "Ranchi", "Sahebganj", "Saraikela Kharsawan", "Simdega", "West Singhbhum"]},
  {"state": "Kerala", "districts": ["Alappuzha", "Ernakulam", "Idukki", "Kannur", "Kasaragod", "Kollam", "Kottayam", "Kozhikode", "Malappuram", "Palakkad", "Pathanamthitta", "Thiruvananthapuram", "Thrissur", "Wayanad"]},
  {"state": "Ladakh", "districts": ["Kargil", "Leh Ladakh"]},
  {"state": "Lakshadweep", "districts": ["Lakshadweep District"]},
  {"state": "Karnataka", "districts": ["Bagalkote", "Ballari", "Belagavi", "Bengaluru Rural", "Bengaluru South", "Bengaluru Urban", "Bidar", "Chamarajanagar", "Chikkaballapura", "Chikkamagaluru", "Chitradurga", "Dakshina Kannada", "Davanagere", "Dharwad", "Gadag", "Hassan", "Haveri", "Kalaburagi", "Kodagu", "Kolar", "Koppal", "Mandya", "Mysuru", "Raichur", "Shivamogga", "Tumakuru", "Udupi", "Uttara Kannada", "Vijayanagara", "Vijayapura", "Yadgir"]},
  {"state": "Himachal Pradesh", "districts": ["Bilaspur", "Chamba", "Hamirpur", "Kangra", "Kinnaur", "Kullu", "Lahaul And Spiti", "Mandi", "Shimla", "Sirmaur", "Solan", "Una"]},
  {"state": "Manipur", "districts": ["Bishnupur", "Chandel", "Churachandpur", "Imphal East", "Imphal West", "Jiribam", "Kakching", "Kamjong", "Kangpokpi", "Noney", "Pherzawl", "Senapati", "Tamenglong", "Tengnoupal", "Thoubal", "Ukhrul"]},
  {"state": "Meghalaya", "districts": ["East Garo Hills", "East Jaintia Hills", "East Khasi Hills", "Eastern West Khasi Hills", "North Garo Hills", "Ri Bhoi", "South Garo Hills", "South West Garo Hills", "South West Khasi Hills", "West Garo Hills", "West Jaintia Hills", "West Khasi Hills"]},
  {"state": "Mizoram", "districts": ["Aizawl", "Champhai", "Hnahthial", "Khawzawl", "Kolasib", "Lawngtlai", "Lunglei", "Mamit", "Saitual", "Serchhip", "Siaha"]},
  {"state": "Maharashtra", "districts": ["Ahilyanagar", "Akola", "Amravati", "Beed", "Bhandara", "Buldhana", "Chandrapur", "Chhatrapati Sambhajinagar", "Dharashiv", "Dhule", "Gadchiroli", "Gondia", "Hingoli", "Jalgaon", "Jalna", "Kolhapur", "Latur", "Mumbai", "Mumbai Suburban", "Nagpur", "Nanded", "Nandurbar", "Nashik", "Palghar", "Parbhani", "Pune", "Raigad", "Ratnagiri", "Sangli", "Satara", "Sindhudurg", "Solapur", "Thane", "Wardha", "Washim", "Yavatmal"]},
  {"state": "Nagaland", "districts": ["Chumoukedima", "Dimapur", "Kiphire", "Kohima", "Longleng", "Meluri", "Mokokchung", "Mon", "Niuland", "Noklak", "Peren", "Phek", "Shamator", "Tseminyu", "Tuensang", "Wokha", "Zunheboto"]},
  {"state": "Puducherry", "districts": ["Karaikal", "Puducherry"]},
  {"state": "Punjab", "districts": ["Amritsar", "Barnala", "Bathinda", "Faridkot", "Fatehgarh Sahib", "Fazilka", "Ferozepur", "Gurdaspur", "Hoshiarpur", "Jalandhar", "Kapurthala", "Ludhiana", "Malerkotla", "Mansa", "Moga", "Pathankot", "Patiala", "Rupnagar", "S.A.S Nagar", "Sangrur", "Shahid Bhagat Singh Nagar", "Sri Muktsar Sahib", "Tarn Taran"]},
  {"state": "Madhya Pradesh", "districts": ["Agar-Malwa", "Alirajpur", "Anuppur", "Ashoknagar", "Balaghat", "Barwani", "Betul", "Bhind", "Bhopal", "Burhanpur", "Chhatarpur", "Chhindwara", "Damoh", "Datia", "Dewas", "Dhar", "Dindori", "Guna", "Gwalior", "Harda", "Indore", "Jabalpur", "Jhabua", "Katni", "Khandwa (East Nimar)", "Khargone (West Nimar)", "MAUGANJ", "Maihar", "Mandla", "Mandsaur", "Morena", "Narmadapuram", "Narsimhapur", "Neemuch", "Niwari", "Pandhurna", "Panna", "Raisen", "Rajgarh", "Ratlam", "Rewa", "Sagar", "Satna", "Sehore", "Seoni", "Shahdol", "Shajapur", "Sheopur", "Shivpuri", "Sidhi", "Singrauli", "Tikamgarh", "Ujjain", "Umaria", "Vidisha"]},
  {"state": "Odisha", "districts": ["Anugola", "Balangir", "Baleshwar", "Baragada", "Bhadrak", "Boudh", "Debagada", "Dhenkanal", "Gajapati", "Ganjam", "Jagatsinghapur", "Jajpur", "Jharsuguda", "Kalahandi", "Kandhamala", "Kataka", "Kendrapada", "Kendujhar", "Khordha", "Koraput", "Malkangiri", "Mayurbhanj", "Nabarangpur", "Nayagada", "Nuapada", "Puri", "Rayagada", "Sambalpur", "Subarnapur", "Sundaragada"]},
  {"state": "Sikkim", "districts": ["Gangtok", "Gyalshing", "Mangan", "Namchi", "Pakyong", "Soreng"]},
  {"state": "Telangana", "districts": ["Adilabad", "Bhadradri Kothagudem", "Hanumakonda", "Hyderabad", "Jagitial", "Jangoan", "Jayashankar Bhupalapally", "Jogulamba Gadwal", "Kamareddy", "Karimnagar", "Khammam", "Kumuram Bheem Asifabad", "Mahabubabad", "Mahabubnagar", "Mancherial", "Medak", "Medchal Malkajgiri", "Mulugu", "Nagarkurnool", "Nalgonda", "Narayanpet", "Nirmal", "Nizamabad", "Peddapalli", "Rajanna Sircilla", "Ranga Reddy", "Sangareddy", "Siddipet", "Suryapet", "Vikarabad", "Wanaparthy", "Warangal", "Yadadri Bhuvanagiri"]},
  {"state": "Tripura", "districts": ["Dhalai", "Gomati", "Khowai", "North Tripura", "Sepahijala", "South Tripura", "Unakoti", "West Tripura"]},
  {"state": "Rajasthan", "districts": ["Ajmer", "Alwar", "Balotra", "Banswara", "Baran", "Barmer", "Beawar", "Bharatpur", "Bhilwara", "Bikaner", "Bundi", "Chittorgarh", "Churu", "Dausa", "Deeg", "Dholpur", "Didwana-Kuchaman", "Dungarpur", "Ganganagar", "Hanumangarh", "Jaipur", "Jaisalmer", "Jalore", "Jhalawar", "Jhunjhunu", "Jodhpur", "Karauli", "Khairthal-Tijara", "Kota", "Kotputli-Behror", "Nagaur", "Pali", "Phalodi", "Pratapgarh", "Rajsamand", "Salumbar", "Sawai Madhopur", "Sikar", "Sirohi", "Tonk", "Udaipur"]},
  {"state": "Uttarakhand", "districts": ["Almora", "Bageshwar", "Chamoli", "Champawat", "Dehradun", "Haridwar", "Nainital", "Pauri Garhwal", "Pithoragarh", "Rudraprayag", "Tehri Garhwal", "Udham Singh Nagar", "Uttarkashi"]},
  {"state": "Tamil Nadu", "districts": ["Ariyalur", "Chengalpattu", "Chennai", "Coimbatore", "Cuddalore", "Dharmapuri", "Dindigul", "Erode", "Kallakurichi", "Kancheepuram", "Kanniyakumari", "Karur", "Krishnagiri", "Madurai", "Mayiladuthurai", "Nagapattinam", "Namakkal", "Perambalur", "Pudukkottai", "Ramanathapuram", "Ranipet", "Salem", "Sivaganga", "Tenkasi", "Thanjavur", "The Nilgiris", "Theni", "Thiruvallur", "Thiruvarur", "Thoothukkudi", "Tiruchirappalli", "Tirunelveli", "Tirupathur", "Tiruppur", "Tiruvannamalai", "Vellore", "Viluppuram", "Virudhunagar"]},
  {"state": "West Bengal", "districts": ["Alipurduar", "Bankura", "Birbhum", "Cooch Behar", "Dakshin Dinajpur", "Darjeeling", "Hooghly", "Howrah", "Jalpaiguri", "Jhargram", "Kalimpong", "Kolkata", "Malda", "Murshidabad", "Nadia", "North 24 Parganas", "Paschim Bardhaman", "Paschim Medinipur", "Purba Bardhaman", "Purba Medinipur", "Purulia", "South 24 Parganas", "Uttar Dinajpur"]},
  {"state": "Uttar Pradesh", "districts": ["Agra", "Aligarh", "Ambedkar Nagar", "Amethi", "Amroha", "Auraiya", "Ayodhya", "Azamgarh", "Baghpat", "Bahraich", "Ballia", "Balrampur", "Banda", "Bara Banki", "Bareilly", "Basti", "Bhadohi", "Bijnor", "Budaun", "Bulandshahr", "Chandauli", "Chitrakoot", "Deoria", "Etah", "Etawah", "Farrukhabad", "Fatehpur", "Firozabad", "Gautam Buddha Nagar", "Ghaziabad", "Ghazipur", "Gonda", "Gorakhpur", "Hamirpur", "Hapur", "Hardoi", "Hathras", "Jalaun", "Jaunpur", "Jhansi", "Kannauj", "Kanpur Dehat", "Kanpur Nagar", "Kasganj", "Kaushambi", "Kheri", "Kushinagar", "Lalitpur", "Lucknow", "Mahoba", "Mahrajganj", "Mainpuri", "Mathura", "Mau", "Meerut", "Mirzapur", "Moradabad", "Muzaffarnagar", "Pilibhit", "Pratapgarh", "Prayagraj", "Rae Bareli", "Rampur", "Saharanpur", "Sambhal", "Sant Kabir Nagar", "Shahjahanpur", "Shamli", "Shrawasti", "Siddharthnagar", "Sitapur", "Sonbhadra", "Sultanpur", "Unnao", "Varanasi"]}
]

STATE_DEFAULTS = {
    'Uttar Pradesh': 756998, 'Maharashtra': 604972, 'Gujarat': 439055,
    'Tamil Nadu': 357744, 'Karnataka': 327423, 'Delhi': 323265,
    'Rajasthan': 311853, 'Bihar': 235905, 'Haryana': 227734,
    'West Bengal': 225426, 'Telangana': 198152, 'Madhya Pradesh': 187836,
    'Andhra Pradesh': 194520, 'Kerala': 168430, 'Punjab': 154200,
    'Odisha': 112650, 'Jharkhand': 98400, 'Chhattisgarh': 89200,
    'Uttarakhand': 76500, 'Assam': 68300, 'Himachal Pradesh': 46200,
    'Goa': 38900, 'Jammu and Kashmir': 34500, 'Chandigarh': 29400,
    'Puducherry': 19200, 'Tripura': 14300, 'Meghalaya': 10800,
    'Nagaland': 8400, 'Manipur': 7600, 'Arunachal Pradesh': 6200,
    'Sikkim': 5400, 'Mizoram': 4800,
    'Dadra and Nagar Haveli and Daman and Diu': 5100,
    'Ladakh': 3900, 'Andaman and Nicobar Islands': 3100, 'Lakshadweep': 1400
}

PROMINENT_DISTRICTS = {
    'Mumbai': 3.2, 'Mumbai Suburban': 2.8, 'Pune': 2.6, 'Thane': 2.0, 'Nagpur': 1.6,
    'Ahmedabad': 2.8, 'Surat': 2.5, 'Vadodara': 1.8, 'Rajkot': 1.5,
    'Bengaluru Urban': 3.5, 'Bengaluru Rural': 1.5, 'Mysuru': 1.3,
    'Chennai': 3.2, 'Coimbatore': 2.0, 'Madurai': 1.4, 'Tiruppur': 1.3,
    'Hyderabad': 3.4, 'Ranga Reddy': 2.2, 'Medchal Malkajgiri': 1.8,
    'Kolkata': 3.2, 'Howrah': 1.8, 'North 24 Parganas': 1.7,
    'Jaipur': 3.0, 'Jodhpur': 1.6, 'Kota': 1.4, 'Udaipur': 1.3,
    'Lucknow': 2.5, 'Gautam Buddha Nagar': 2.4, 'Kanpur Nagar': 2.2, 'Ghaziabad': 2.0, 'Agra': 1.8, 'Varanasi': 1.7, 'Prayagraj': 1.6,
    'Patna': 3.2, 'Muzaffarpur': 1.5, 'Gaya': 1.4,
    'Indore': 3.0, 'Bhopal': 2.5, 'Jabalpur': 1.6, 'Gwalior': 1.5,
    'Gurugram': 3.2, 'Faridabad': 2.2, 'Panipat': 1.5,
    'Ludhiana': 2.8, 'Amritsar': 2.0, 'Jalandhar': 1.8, 'S.A.S Nagar': 1.7,
    'Ernakulam': 2.8, 'Thiruvananthapuram': 2.2, 'Kozhikode': 1.8,
    'Khordha': 2.6, 'Cuttack': 1.8,
    'Ranchi': 2.8, 'Dhanbad': 2.0, 'East Singhbum': 2.0,
    'Raipur': 2.8, 'Durg': 1.8, 'Bilaspur': 1.5,
    'Dehradun': 2.8, 'Haridwar': 2.0, 'Udham Singh Nagar': 1.7,
    'Kamrup Metro': 3.0,
    'New Delhi': 1.8, 'Central': 1.5, 'South': 1.5, 'North': 1.3
}

def get_district_leads(sname, dname, stotal, ndistricts):
    base_share = stotal / max(ndistricts, 1)
    mult = PROMINENT_DISTRICTS.get(dname)
    if mult is None:
        h = int(hashlib.md5(f"{sname}:{dname}".encode()).hexdigest(), 16)
        mult = 0.65 + ((h % 55) / 100.0)
    return max(int(base_share * mult), 850)

def init_db():
    db = sqlite3.connect(DB_PATH)
    db.executescript(SCHEMA)
    db.execute("INSERT OR IGNORE INTO admin_users(username,password_hash) VALUES(?,?)",
               (ADMIN_USER, generate_password_hash(ADMIN_PASS)))

    # Seed default settings
    default_settings = [
        ("upi_id", UPI_ID_DEFAULT),
        ("upi_name", UPI_NAME_DEFAULT),
        ("call_number", "+917648990004"),
        ("call_display", "+91 76489 90004"),
        ("whatsapp_number", WHATSAPP_NUMBER_DEFAULT),
        ("whatsapp_display", WHATSAPP_DISPLAY_DEFAULT),
        ("whatsapp_default_msg", WHATSAPP_MSG_DEFAULT),
        ("qr_mode", "dynamic"),
        ("custom_qr_file", ""),
    ]
    for k, v in default_settings:
        db.execute("INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)", (k, v))

    # Ensure all 36 Indian states exist
    for loc in LOCATIONS:
        sname = loc["state"].strip()
        sslug = slugify(sname)
        def_leads = STATE_DEFAULTS.get(sname, 10000)
        db.execute("""INSERT INTO states(name, slug, total_leads, price)
                      VALUES(?, ?, ?, ?)
                      ON CONFLICT(name) DO UPDATE SET
                      slug=excluded.slug,
                      total_leads=CASE WHEN states.total_leads = 0 THEN excluded.total_leads ELSE states.total_leads END""",
                   (sname, sslug, def_leads, PRICES.get("state", 999)))

    # Fetch updated state mappings
    rows = db.execute("SELECT id, name, total_leads FROM states").fetchall()
    s_map = {r[1]: (r[0], r[2]) for r in rows}

    # Ensure all 784 districts exist in cities
    for loc in LOCATIONS:
        sname = loc["state"].strip()
        if sname not in s_map: continue
        sid, stotal = s_map[sname]
        districts = loc.get("districts", [])
        nd = len(districts)
        for d in districts:
            dname = d.strip()
            dslug = slugify(dname)
            dleads = get_district_leads(sname, dname, stotal, nd)
            db.execute("""INSERT INTO cities(state_id, name, slug, total_leads, price)
                          VALUES(?, ?, ?, ?, ?)
                          ON CONFLICT(state_id, name) DO UPDATE SET
                          slug=excluded.slug,
                          total_leads=CASE WHEN cities.total_leads = 0 THEN excluded.total_leads ELSE cities.total_leads END""",
                       (sid, dname, dslug, dleads, PRICES.get("city", 599)))

    # Insert default blog posts if table empty
    if db.execute("SELECT COUNT(*) FROM blog_posts").fetchone()[0] == 0:
        for t, ex, b in [
            ("Choosing the Right GST Database Provider: 2026 Checklist",
             "Not every provider delivers the same quality. What to check before you buy.",
             "Check freshness of data, field coverage, refund policy and sample quality before buying any B2B database."),
            ("HSN Code + GST Data for Precise B2B Targeting",
             "HSN codes tell you what a business actually deals in.",
             "Pairing HSN with GST registration data turns a generic list into a precisely targetable one."),
            ("How to Reach Newly Registered GST Holders First",
             "New GST businesses are actively setting up operations.",
             "Newly registered businesses are the most receptive B2B audience. Reach them early.")]:
            db.execute("INSERT OR IGNORE INTO blog_posts(title,slug,excerpt,body) VALUES(?,?,?,?)",
                       (t, slugify(t), ex, b))
    db.commit()
    db.close()


# ================= HELPERS =================
def admin_required(f):
    @wraps(f)
    def w(*a, **k):
        if not session.get("admin"): return redirect(url_for("admin_login"))
        return f(*a, **k)
    return w

def new_order_id():
    return "TB" + datetime.now().strftime("%y%m%d") + "-" + secrets.token_hex(3).upper()

def calc_amount(ptype, state_id=None, city_id=None, category_id=None):
    db = get_db()
    if ptype == "state" and state_id:
        r = db.execute("SELECT price FROM states WHERE id=?", (state_id,)).fetchone()
        amt = r["price"] if r else PRICES["state"]
    elif ptype == "city" and city_id:
        r = db.execute("SELECT price FROM cities WHERE id=?", (city_id,)).fetchone()
        amt = r["price"] if r else PRICES["city"]
    else:
        amt = PRICES.get(ptype, 0)
    if category_id: amt += CATEGORY_ADDON
    return amt

def get_qr_url(order_id, amount):
    import urllib.parse
    cfg = get_all_settings()
    if cfg.get("qr_mode") == "custom" and cfg.get("custom_qr_file"):
        custom_file = cfg.get("custom_qr_file")
        custom_path = os.path.join(BASE, "static", "qr", custom_file)
        if os.path.exists(custom_path):
            return url_for("static", filename=f"qr/{custom_file}")

    upi_id = cfg.get("upi_id", UPI_ID_DEFAULT)
    upi_name = cfg.get("upi_name", UPI_NAME_DEFAULT)
    link = f"upi://pay?pa={upi_id}&pn={urllib.parse.quote(upi_name)}&am={amount}&cu=INR&tn=Order%20{order_id}"
    qr_dir = os.path.join(BASE, "static", "qr")
    os.makedirs(qr_dir, exist_ok=True)
    filename = f"{order_id}.png"
    filepath = os.path.join(qr_dir, filename)
    qrcode.make(link).save(filepath)
    return url_for("static", filename=f"qr/{filename}")

def make_qr(order_id, amount):
    return get_qr_url(order_id, amount)

def csv_response(df, filename):
    buf = io.StringIO(); df.to_csv(buf, index=False)
    mem = io.BytesIO(buf.getvalue().encode("utf-8"))
    return send_file(mem, as_attachment=True, download_name=filename, mimetype="text/csv")

# ================= PUBLIC ROUTES =================
@app.route("/favicon.ico")
def favicon():
    fav_ico = os.path.join(BASE, "static", "favicon.ico")
    if os.path.isfile(fav_ico):
        return send_file(fav_ico, mimetype="image/x-icon")
    return send_file(os.path.join(BASE, "static", "logo.png"), mimetype="image/png")

@app.route("/")
def home():
    db = get_db()
    states = db.execute("SELECT * FROM states ORDER BY total_leads DESC LIMIT 12").fetchall()
    all_states_list = db.execute("SELECT id, name, slug, total_leads FROM states ORDER BY name").fetchall()
    cities = db.execute("""SELECT c.*, s.name sname, s.slug sslug FROM cities c
        JOIN states s ON s.id=c.state_id ORDER BY c.total_leads DESC LIMIT 24""").fetchall()
    total = db.execute("SELECT COALESCE(SUM(total_leads),0) t FROM states").fetchone()[0]
    nstates = db.execute("SELECT COUNT(*) c FROM states").fetchone()[0]
    ncities = db.execute("SELECT COUNT(*) c FROM cities").fetchone()[0]
    return render_template("index.html", states=states, all_states=all_states_list, cities=cities,
                           total=total, nstates=nstates, ncities=ncities)

@app.route("/gst-database")
def all_states():
    db = get_db()
    states = db.execute("""SELECT s.*, COUNT(c.id) as district_count
                           FROM states s
                           LEFT JOIN cities c ON c.state_id=s.id
                           GROUP BY s.id
                           ORDER BY s.name""").fetchall()
    cities = db.execute("""SELECT c.*, s.name as sname, s.slug as sslug
                           FROM cities c
                           JOIN states s ON s.id=c.state_id
                           ORDER BY s.name, c.name""").fetchall()
    total = db.execute("SELECT COALESCE(SUM(total_leads),0) FROM states").fetchone()[0]
    return render_template("states.html", states=states, cities=cities,
                           total_leads=total, nstates=len(states), ncities=len(cities))

@app.route("/gst-districts")
def all_districts():
    return redirect(url_for("all_states") + "#districts")

@app.route("/api/locations")
def api_locations():
    db = get_db()
    states = db.execute("SELECT id, name, slug, total_leads, price FROM states ORDER BY name").fetchall()
    cities = db.execute("""SELECT c.id, c.name, c.slug, c.total_leads, c.price, s.name as sname, s.slug as sslug
                           FROM cities c JOIN states s ON s.id=c.state_id ORDER BY s.name, c.name""").fetchall()
    return {
        "states": [dict(s) for s in states],
        "districts": [dict(c) for c in cities]
    }

@app.route("/gst-database/<sslug>")
def state_page(sslug):
    db = get_db()
    st = db.execute("SELECT * FROM states WHERE slug=?", (sslug,)).fetchone()
    if not st: abort(404)
    cities = db.execute("SELECT * FROM cities WHERE state_id=? ORDER BY total_leads DESC",
                        (st["id"],)).fetchall()
    return render_template("location.html", loc=st, cities=cities, is_state=True)

@app.route("/gst-database/<sslug>/<cslug>")
def city_page(sslug, cslug):
    db = get_db()
    st = db.execute("SELECT * FROM states WHERE slug=?", (sslug,)).fetchone()
    if not st: abort(404)
    ct = db.execute("SELECT * FROM cities WHERE slug=? AND state_id=?", (cslug, st["id"])).fetchone()
    if not ct: abort(404)
    other_cities = db.execute("""SELECT c.*, s.slug as sslug FROM cities c
                                 JOIN states s ON s.id=c.state_id
                                 WHERE c.state_id=? AND c.id!=?
                                 ORDER BY c.total_leads DESC LIMIT 16""",
                              (st["id"], ct["id"])).fetchall()
    return render_template("location.html", loc=ct, state=st, cities=[], other_cities=other_cities, is_state=False)


@app.route("/faq")
def faq(): return render_template("faq.html")

@app.route("/privacy-policy")
def privacy_policy():
    return render_template("privacy.html")

@app.route("/blog")
def blog():
    posts = get_db().execute("SELECT * FROM blog_posts ORDER BY id DESC").fetchall()
    return render_template("blog.html", posts=posts)

@app.route("/blog/<slug>")
def blog_post(slug):
    p = get_db().execute("SELECT * FROM blog_posts WHERE slug=?", (slug,)).fetchone()
    if not p: abort(404)
    return render_template("post.html", p=p)

# ---------- CHECKOUT ----------
@app.route("/checkout", methods=["GET", "POST"])
def checkout():
    db = get_db()
    if request.method == "POST":
        ptype = request.form.get("ptype", "")
        state_id = request.form.get("state_id") or None
        city_id  = request.form.get("city_id") or None
        category_id = request.form.get("category_id") or None
        if ptype not in PRICES:
            flash("Package select karo"); return redirect(url_for("checkout"))
        if ptype == "state" and not state_id:
            flash("State select karo"); return redirect(url_for("checkout"))
        if ptype == "city" and not city_id:
            flash("City select karo"); return redirect(url_for("checkout"))
        # cleanup — galat combination me amount galat na bane
        if ptype == "pan_india": state_id = city_id = None
        if ptype == "state": city_id = None
        if ptype == "city" and city_id:
            r = db.execute("SELECT state_id FROM cities WHERE id=?", (city_id,)).fetchone()
            state_id = r["state_id"] if r else None
        state_id = int(state_id) if state_id else None
        city_id = int(city_id) if city_id else None
        category_id = int(category_id) if category_id else None
        amount = calc_amount(ptype, state_id, city_id, category_id)
        oid = new_order_id()
        db.execute("""INSERT INTO orders(order_id,package_type,state_id,city_id,category_id,
            amount,buyer_name,buyer_email,buyer_phone,download_token)
            VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (oid, ptype, state_id, city_id, category_id, amount,
             request.form["name"], request.form["email"], request.form["phone"],
             secrets.token_urlsafe(24)))
        db.commit()
        return redirect(url_for("pay", order_id=oid))

    states = db.execute("SELECT * FROM states ORDER BY name").fetchall()
    cities = db.execute("""SELECT c.*, s.id sid FROM cities c
        JOIN states s ON s.id=c.state_id ORDER BY c.name""").fetchall()
    cats = db.execute("SELECT * FROM categories ORDER BY name").fetchall()
    tree = [{"id": s["id"], "name": s["name"], "price": s["price"],
             "cities": [{"id": c["id"], "name": c["name"], "price": c["price"]}
                        for c in cities if c["sid"] == s["id"]]} for s in states]
    return render_template("checkout.html", tree=tree, cats=cats,
                           PAN=PRICES["pan_india"], ADDON=CATEGORY_ADDON)

# ---------- PAYMENT ----------
@app.route("/pay/<order_id>")
def pay(order_id):
    db = get_db()
    o = db.execute("SELECT * FROM orders WHERE order_id=?", (order_id,)).fetchone()
    if not o: abort(404)
    cfg = get_all_settings()
    qr_url = get_qr_url(order_id, o["amount"])
    return render_template("payment.html", o=o, upi=cfg["upi_id"], upi_name=cfg["upi_name"], qr_image_url=qr_url)

@app.route("/pay/<order_id>/utr", methods=["POST"])
def submit_utr(order_id):
    utr = request.form.get("utr", "").strip()
    if not utr:
        flash("UTR/Transaction ID required"); return redirect(url_for("pay", order_id=order_id))
    status = "verified" if AUTO_VERIFY else "submitted"
    get_db().execute("UPDATE orders SET utr_number=?, status=? WHERE order_id=?",
                     (utr, status, order_id))
    get_db().commit()
    flash("Payment submitted! Verification ke baad download activate ho jayega.")
    return redirect(url_for("order_status", order_id=order_id))

@app.route("/order/<order_id>")
def order_status(order_id):
    o = get_db().execute("SELECT * FROM orders WHERE order_id=?", (order_id,)).fetchone()
    if not o: abort(404)
    return render_template("order_status.html", o=o)

# ---------- DOWNLOAD ----------
@app.route("/download/<token>")
def download(token):
    db = get_db()
    o = db.execute("SELECT * FROM orders WHERE download_token=?", (token,)).fetchone()
    if not o or o["status"] != "verified": abort(403)
    if o["download_count"] >= MAX_DOWNLOADS: abort(403)
    conds, params = [], []
    if o["state_id"]:    conds.append("l.state_id=?");    params.append(o["state_id"])
    if o["city_id"]:     conds.append("l.city_id=?");     params.append(o["city_id"])
    if o["category_id"]: conds.append("l.category_id=?"); params.append(o["category_id"])
    q = """SELECT l.company_name, s.name AS state, c.name AS city, cat.name AS category,
           l.address, l.email, l.phone1, l.phone2, l.phone3, l.owner_name, l.gstin
           FROM leads l
           LEFT JOIN states s ON s.id=l.state_id
           LEFT JOIN cities c ON c.id=l.city_id
           LEFT JOIN categories cat ON cat.id=l.category_id"""
    if conds: q += " WHERE " + " AND ".join(conds)
    df = pd.read_sql_query(q, db)
    db.execute("UPDATE orders SET download_count=download_count+1 WHERE id=?", (o["id"],))
    db.commit()
    return csv_response(df, f"gst_{o['package_type']}_{o['order_id']}.csv")


# ================= FREE SAMPLE (Instant Access & Direct Email Notification) =================
SAMPLE_FILE_PATH = os.path.join(BASE, "static", "samples", "datnex_free_sample.xlsx")
SAMPLE_NOTIFICATION_EMAIL = "zuberbaig09876@gmail.com"

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import threading

def send_sample_lead_email(lead_email, lead_phone, ip=""):
    """
    Sends instant lead alert email to zuberbaig09876@gmail.com when a visitor enrolls for a free sample.
    Dispatched asynchronously in a daemon thread so the applicant's experience is instant.
    zuberbaig09876@gmail.com is strictly kept backend-only and never exposed to the public frontend.
    """
    def _worker():
        try:
            cfg = get_all_settings()
            smtp_host = cfg.get("smtp_host") or os.environ.get("SMTP_HOST", "smtp.gmail.com")
            smtp_port = int(cfg.get("smtp_port") or os.environ.get("SMTP_PORT", 587))
            smtp_user = cfg.get("smtp_user") or os.environ.get("SMTP_USER", "")
            smtp_pass = cfg.get("smtp_pass") or os.environ.get("SMTP_PASS", "")
            from_email = cfg.get("smtp_from") or os.environ.get("SMTP_FROM", smtp_user or "leads@datnex.in")

            now_str = datetime.now().strftime("%d %b %Y, %I:%M %p")

            subject = f"🔥 New Free Sample Lead: +91 {lead_phone} | datnex"
            
            body_text = f"""New Free Sample Request on datnex!
---------------------------------------------
Phone Number : +91 {lead_phone}
Email Address: {lead_email}
Date & Time  : {now_str}
IP Address   : {ip or 'N/A'}

WhatsApp Lead: https://wa.me/91{lead_phone}
Call Lead    : tel:+91{lead_phone}
---------------------------------------------
datnex Automated Lead Notification System
"""

            body_html = f"""
            <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;max-width:600px;margin:0 auto;padding:24px;background:#f8fafc;border-radius:14px;border:1px solid #e2e8f0">
              <div style="background:linear-gradient(135deg,#1769e0,#0b2545);padding:22px;border-radius:10px;color:#fff;text-align:center">
                <h1 style="margin:0;font-size:22px;font-weight:800">🔥 New Free Sample Lead!</h1>
                <p style="margin:6px 0 0;font-size:13px;opacity:.9">A buyer has just requested a GST database sample on datnex</p>
              </div>
              <div style="background:#fff;margin-top:16px;padding:24px;border-radius:10px;border:1px solid #e2e8f0">
                <table style="width:100%;border-collapse:collapse;font-size:14px">
                  <tr style="border-bottom:1px solid #f1f5f9">
                    <td style="padding:12px 0;color:#64748b;font-weight:600;width:130px">📱 Phone Number</td>
                    <td style="padding:12px 0;font-size:16px;font-weight:800;color:#0b2545">+91 {lead_phone}</td>
                  </tr>
                  <tr style="border-bottom:1px solid #f1f5f9">
                    <td style="padding:12px 0;color:#64748b;font-weight:600">📧 Email Address</td>
                    <td style="padding:12px 0;color:#0f172a;font-weight:600">{lead_email}</td>
                  </tr>
                  <tr style="border-bottom:1px solid #f1f5f9">
                    <td style="padding:12px 0;color:#64748b;font-weight:600">🕒 Request Time</td>
                    <td style="padding:12px 0;color:#334155">{now_str}</td>
                  </tr>
                  <tr>
                    <td style="padding:12px 0;color:#64748b;font-weight:600">🌐 IP Address</td>
                    <td style="padding:12px 0;color:#64748b;font-family:monospace">{ip or 'N/A'}</td>
                  </tr>
                </table>
                <div style="margin-top:22px;display:flex;gap:10px">
                  <a href="https://wa.me/91{lead_phone}" style="background:#25d366;color:#fff;padding:12px 20px;border-radius:8px;text-decoration:none;font-weight:700;font-size:13px;display:inline-block">💬 WhatsApp Lead</a>
                  <a href="tel:+91{lead_phone}" style="background:#1769e0;color:#fff;padding:12px 20px;border-radius:8px;text-decoration:none;font-weight:700;font-size:13px;display:inline-block;margin-left:8px">📞 Call Lead</a>
                </div>
              </div>
              <p style="margin-top:14px;font-size:11px;color:#94a3b8;text-align:center">Confidential Notification • datnex System</p>
            </div>
            """

            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = from_email
            msg["To"] = SAMPLE_NOTIFICATION_EMAIL
            msg.attach(MIMEText(body_text, "plain"))
            msg.attach(MIMEText(body_html, "html"))

            if smtp_user and smtp_pass:
                if smtp_port == 465:
                    server = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=10)
                else:
                    server = smtplib.SMTP(smtp_host, smtp_port, timeout=10)
                    server.starttls()
                server.login(smtp_user, smtp_pass)
                server.sendmail(from_email, [SAMPLE_NOTIFICATION_EMAIL], msg.as_string())
                server.quit()
                print(f"[MAIL] Free sample notification successfully sent to {SAMPLE_NOTIFICATION_EMAIL} via SMTP.")
            else:
                try:
                    server = smtplib.SMTP("localhost", 25, timeout=3)
                    server.sendmail(from_email, [SAMPLE_NOTIFICATION_EMAIL], msg.as_string())
                    server.quit()
                    print(f"[MAIL] Free sample notification sent via localhost to {SAMPLE_NOTIFICATION_EMAIL}.")
                except Exception:
                    print(f"[MAIL LEAD] Enrolled: Phone={lead_phone}, Email={lead_email} -> Notification queued for {SAMPLE_NOTIFICATION_EMAIL}. Note: Configure SMTP credentials in Admin Settings.")
        except Exception as e:
            print(f"[MAIL ERROR] Could not dispatch email to {SAMPLE_NOTIFICATION_EMAIL}: {e}")

    threading.Thread(target=_worker, daemon=True).start()


@app.route("/free-sample", methods=["GET", "POST"])
def free_sample():
    step = request.args.get("step", "1")

    # Form submission: email and phone provided
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        phone = request.form.get("phone", "").strip()

        # Validation
        if not email or "@" not in email:
            flash("Please enter a valid email address.")
            return render_template("free_sample.html", step="1", email=email, phone=phone)
        if not phone or not re.fullmatch(r"[6-9]\d{9}", phone.replace(" ", "").replace("-", "")):
            flash("Please enter a valid 10-digit Indian mobile number.")
            return render_template("free_sample.html", step="1", email=email, phone=phone)

        clean_phone = phone.replace(" ", "").replace("-", "")[-10:]
        client_ip = request.headers.get("X-Forwarded-For", request.remote_addr or "")

        # 1. Store lead in database for permanent record
        try:
            db = get_db()
            db.execute("INSERT INTO sample_requests(email, phone, ip) VALUES(?, ?, ?)",
                       (email, clean_phone, client_ip))
            db.commit()
        except Exception as e:
            print(f"[DB ERROR] Storing sample lead: {e}")

        # 2. Dispatch instant notification email to zuberbaig09876@gmail.com
        send_sample_lead_email(email, clean_phone, client_ip)

        # 3. Store session authorization
        session["sample_authorized"] = True
        session["sample_email"] = email
        session["sample_phone"] = clean_phone

        return render_template("free_sample.html", step="success", email=email, phone=clean_phone)

    # GET request
    if step == "success" and session.get("sample_authorized"):
        return render_template("free_sample.html", step="success",
                               email=session.get("sample_email", ""),
                               phone=session.get("sample_phone", ""))
    return render_template("free_sample.html", step="1")


@app.route("/free-sample/download")
def free_sample_download():
    # Serve static sample file if present
    if os.path.isfile(SAMPLE_FILE_PATH) and os.path.getsize(SAMPLE_FILE_PATH) > 0:
        fname = os.path.basename(SAMPLE_FILE_PATH)
        return send_file(SAMPLE_FILE_PATH, as_attachment=True, download_name=fname)

    # Fallback: generate clean 100-record CSV
    db = get_db()
    n = db.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
    if n >= 100:
        df = pd.read_sql_query("""SELECT l.company_name, s.name AS state, c.name AS city,
            l.email, l.phone1, l.owner_name, l.gstin FROM leads l
            LEFT JOIN states s ON s.id=l.state_id
            LEFT JOIN cities c ON c.id=l.city_id LIMIT 100""", db)
    else:
        df = pd.DataFrame([{
            "company_name": f"Demo Enterprises {i+1}", "state": "Uttar Pradesh",
            "city": "Lucknow", "email": f"demo{i+1}@example.com",
            "phone1": f"98765432{i:02d}", "owner_name": f"Owner {i+1}",
            "gstin": f"09ABCDE1234F{i:03d}"[:15].ljust(15, "Z")} for i in range(100)])
    return csv_response(df, "datnex_free_sample.csv")


# ================= ADMIN =================
@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        db = get_db()
        u = db.execute("SELECT * FROM admin_users WHERE username=?",
                       (request.form["username"],)).fetchone()
        if u and check_password_hash(u["password_hash"], request.form["password"]):
            session["admin"] = u["username"]
            return redirect(url_for("admin_dashboard"))
        flash("Galat username/password")
    return render_template("admin/login.html")

@app.route("/admin/logout")
def admin_logout():
    session.pop("admin", None); return redirect(url_for("home"))

@app.route("/admin")
@admin_required
def admin_dashboard():
    db = get_db()
    has_samples = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='sample_requests'").fetchone()
    stats = {
        "leads":   db.execute("SELECT COUNT(*) FROM leads").fetchone()[0],
        "orders":  db.execute("SELECT COUNT(*) FROM orders").fetchone()[0],
        "revenue": db.execute("SELECT COALESCE(SUM(amount),0) FROM orders WHERE status='verified'").fetchone()[0],
        "pending": db.execute("SELECT COUNT(*) FROM orders WHERE status='submitted'").fetchone()[0],
        "samples": db.execute("SELECT COUNT(*) FROM sample_requests").fetchone()[0] if has_samples else 0,
    }
    orders = db.execute("SELECT * FROM orders ORDER BY id DESC LIMIT 10").fetchall()
    sample_leads = db.execute("SELECT * FROM sample_requests ORDER BY id DESC LIMIT 10").fetchall() if has_samples else []
    return render_template("admin/dashboard.html", stats=stats, orders=orders, sample_leads=sample_leads)

# ---------- CSV UPLOAD ----------
HEADER_HINTS = {
    "company_name": ["companyname","company","businessname","firmname","legalname","tradename","name"],
    "gstin":  ["gstin","gstnumber","gstno","gst"],
    "address": ["address","fulladdress","addr","registeredaddress"],
    "email": ["email","emailaddress","mail"],
    "phone1": ["phone1","phone","phonenumber","mobile","contactnumber","contact","number"],
    "phone2": ["phone2","mobile2","alternatenumber"],
    "phone3": ["phone3"],
    "owner_name": ["ownername","owner","contactperson","proprietor"],
    "state": ["state","statename"],
    "city": ["city","town","district"],
    "category": ["category","industry","businesstype","segment"],
}

def map_columns(df):
    norm = {re.sub(r"[^a-z0-9]", "", str(c).lower()): c for c in df.columns}
    out = {}
    for field, hints in HEADER_HINTS.items():
        for h in hints:
            if h in norm: out[field] = norm[h]; break
    return out

def ensure_state(db, name, cache):
    name = name.strip().title()
    if not name: return None
    if name in cache: return cache[name]
    row = db.execute("SELECT id FROM states WHERE name=?", (name,)).fetchone()
    if not row:
        db.execute("INSERT OR IGNORE INTO states(name,slug) VALUES(?,?)", (name, slugify(name)))
        row = db.execute("SELECT id FROM states WHERE name=?", (name,)).fetchone()
    cache[name] = row["id"]; return row["id"]

def ensure_city(db, name, state_id, cache):
    name = name.strip().title()
    if not name or not state_id: return None
    key = (name, state_id)
    if key in cache: return cache[key]
    row = db.execute("SELECT id FROM cities WHERE name=? AND state_id=?", (name, state_id)).fetchone()
    if not row:
        db.execute("INSERT OR IGNORE INTO cities(name,slug,state_id) VALUES(?,?,?)",
                   (name, slugify(name), state_id))
        row = db.execute("SELECT id FROM cities WHERE name=? AND state_id=?", (name, state_id)).fetchone()
    cache[key] = row["id"]; return row["id"]

def ensure_category(db, name):
    name = (name or "").strip().title()
    if not name: return None
    row = db.execute("SELECT id FROM categories WHERE name=?", (name,)).fetchone()
    if not row:
        db.execute("INSERT OR IGNORE INTO categories(name,slug) VALUES(?,?)", (name, slugify(name)))
        row = db.execute("SELECT id FROM categories WHERE name=?", (name,)).fetchone()
    return row["id"]

@app.route("/admin/upload", methods=["GET", "POST"])
@admin_required
def admin_upload():
    db = get_db()
    if request.method == "POST":
        f = request.files.get("file")
        if not f or not f.filename.endswith(".csv"):
            flash("Sirf CSV file upload karo"); return redirect(request.url)
        try:
            df = pd.read_csv(f, dtype=str, keep_default_na=False)
        except UnicodeDecodeError:
            f.stream.seek(0)
            df = pd.read_csv(f, dtype=str, keep_default_na=False, encoding="latin-1")
        df.columns = [re.sub(r"\s+", "_", str(c).strip().lower()) for c in df.columns]
        colmap = map_columns(df)
        if "gstin" not in colmap:
            flash("CSV me GSTIN column nahi mila! Mile hue columns: " + ", ".join(df.columns))
            return redirect(request.url)

        manual_state = request.form.get("state_id") or None
        manual_city  = request.form.get("city_id") or None
        cat_name     = request.form.get("category", "")
        use_csv_cols = request.form.get("mode") == "auto"

        state_cache, city_cache = {}, {}
        skipped = 0
        before = db.total_changes
        recs = []
        for row in df.to_dict("records"):
            gst = str(row.get(colmap["gstin"], "")).strip().upper()
            if not GSTIN_RE.match(gst): skipped += 1; continue
            if use_csv_cols and "state" in colmap:
                s_id = ensure_state(db, str(row.get(colmap["state"], "")), state_cache)
            else: s_id = int(manual_state) if manual_state else None
            if use_csv_cols and "city" in colmap:
                c_id = ensure_city(db, str(row.get(colmap["city"], "")), s_id, city_cache)
            else: c_id = int(manual_city) if manual_city else None
            if use_csv_cols and "category" in colmap:
                cat_id = ensure_category(db, str(row.get(colmap["category"], "")))
            else: cat_id = ensure_category(db, cat_name)

            p1 = str(row.get(colmap.get("phone1"), "")).strip()
            parts = re.split(r"[,/;]", p1)
            if len(parts) > 1 and "phone2" not in colmap:
                p1, p2 = parts[0].strip(), parts[1].strip()
            else:
                p2 = str(row.get(colmap.get("phone2"), "")).strip()
            p3 = str(row.get(colmap.get("phone3"), "")).strip()
            recs.append((
                str(row.get(colmap.get("company_name"), "")).strip(),
                str(row.get(colmap.get("address"), "")).strip(),
                s_id, c_id, cat_id,
                str(row.get(colmap.get("email"), "")).strip(),
                p1, p2, p3,
                str(row.get(colmap.get("owner_name"), "")).strip(), gst))

        db.executemany("""INSERT OR IGNORE INTO leads(company_name,address,state_id,city_id,
            category_id,email,phone1,phone2,phone3,owner_name,gstin)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)""", recs)
        inserted = db.total_changes - before
        skipped = len(recs) - inserted + skipped
        db.execute("UPDATE states SET total_leads=(SELECT COUNT(*) FROM leads WHERE state_id=states.id)")
        db.execute("UPDATE cities SET total_leads=(SELECT COUNT(*) FROM leads WHERE city_id=cities.id)")
        db.execute("INSERT INTO uploads_log(filename,total_rows,inserted,skipped) VALUES(?,?,?,?)",
                   (f.filename, len(df), inserted, skipped))
        db.commit()
        flash(f"Done! Rows: {len(df)} | Inserted: {inserted} | Skipped: {skipped}")
        return redirect(request.url)

    states = db.execute("SELECT * FROM states ORDER BY name").fetchall()
    cities = db.execute("SELECT id, name, state_id FROM cities ORDER BY name").fetchall()
    logs = db.execute("SELECT * FROM uploads_log ORDER BY id DESC LIMIT 10").fetchall()
    return render_template("admin/upload.html", states=states, cities=cities, logs=logs)

@app.route("/admin/orders")
@admin_required
def admin_orders():
    orders = get_db().execute("""SELECT o.*, s.name sname, c.name cname
        FROM orders o LEFT JOIN states s ON s.id=o.state_id
        LEFT JOIN cities c ON c.id=o.city_id ORDER BY o.id DESC""").fetchall()
    return render_template("admin/orders.html", orders=orders)

@app.route("/admin/orders/<int:oid>/update", methods=["POST"])
@admin_required
def admin_order_update(oid):
    db = get_db()
    o = db.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
    if not o:
        flash("Order nahi mila")
        return redirect(url_for("admin_orders"))

    utr = request.form.get("utr", "").strip()
    status = request.form.get("status", "").strip().lower()
    if status not in ("pending", "submitted", "verified", "rejected"):
        status = "pending"

    token = o["download_token"]
    if status == "verified" and not token:
        token = secrets.token_urlsafe(24)

    db.execute("UPDATE orders SET utr_number=?, status=?, download_token=? WHERE id=?",
               (utr if utr else None, status, token, oid))
    db.commit()
    flash(f"Order #{o['order_id']} updated successfully!")
    return redirect(url_for("admin_orders"))

@app.route("/admin/orders/<int:oid>/<action>", methods=["POST"])
@admin_required
def admin_order_action(oid, action):
    db = get_db()
    o = db.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
    if not o:
        return redirect(url_for("admin_orders"))

    if action == "verify":
        token = o["download_token"] or secrets.token_urlsafe(24)
        db.execute("UPDATE orders SET status='verified', download_token=? WHERE id=?", (token, oid))
        flash(f"Order #{o['order_id']} verified successfully!")
    elif action == "reject":
        db.execute("UPDATE orders SET status='rejected' WHERE id=?", (oid,))
        flash(f"Order #{o['order_id']} marked as rejected!")
    elif action == "pending":
        db.execute("UPDATE orders SET status='pending' WHERE id=?", (oid,))
        flash(f"Order #{o['order_id']} marked as pending!")
    elif action == "clear_utr":
        db.execute("UPDATE orders SET utr_number=NULL WHERE id=?", (oid,))
        flash(f"UTR cleared for #{o['order_id']}!")
    elif action == "delete":
        db.execute("DELETE FROM orders WHERE id=?", (oid,))
        flash(f"Order #{o['order_id']} deleted!")
    db.commit()
    return redirect(url_for("admin_orders"))

@app.route("/admin/orders/clear-all", methods=["POST"])
@admin_required
def admin_clear_orders():
    db = get_db()
    db.execute("DELETE FROM orders")
    try:
        db.execute("DELETE FROM sqlite_sequence WHERE name='orders'")
    except Exception:
        pass
    db.commit()
    flash("All test orders and buyer records have been cleared!")
    return redirect(url_for("admin_orders"))

@app.route("/admin/sample-requests/clear-all", methods=["POST"])
@admin_required
def admin_clear_sample_requests():
    db = get_db()
    db.execute("DELETE FROM sample_requests")
    try:
        db.execute("DELETE FROM sqlite_sequence WHERE name='sample_requests'")
    except Exception:
        pass
    db.commit()
    flash("All free sample test leads have been cleared!")
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/leads/clear-all", methods=["POST"])
@admin_required
def admin_clear_leads():
    db = get_db()
    db.execute("DELETE FROM leads")
    try:
        db.execute("DELETE FROM sqlite_sequence WHERE name='leads'")
    except Exception:
        pass
    db.commit()
    flash("All uploaded GST database records cleared successfully!")
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/locations", methods=["GET", "POST"])
@admin_required
def admin_locations():
    db = get_db()
    if request.method == "POST":
        a = request.form.get("action")
        is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.is_json

        if a == "add_state" and request.form.get("name"):
            leads = int(request.form.get("total_leads") or 0)
            db.execute("INSERT OR IGNORE INTO states(name,slug,price,total_leads) VALUES(?,?,?,?)",
                       (request.form["name"].title(), slugify(request.form["name"]),
                        int(request.form.get("price", 999)), leads))
            db.commit()
            flash("State added successfully!")
            return redirect(request.url)

        elif a == "add_city" and request.form.get("name") and request.form.get("state_id"):
            leads = int(request.form.get("total_leads") or 0)
            db.execute("INSERT OR IGNORE INTO cities(name,slug,state_id,price,total_leads) VALUES(?,?,?,?,?)",
                       (request.form["name"].title(), slugify(request.form["name"]),
                        request.form["state_id"], int(request.form.get("price", 599)), leads))
            db.commit()
            flash("District added successfully!")
            return redirect(request.url)

        elif a in ("update_state", "price_state"):
            sid = request.form.get("id")
            total_leads = request.form.get("total_leads")
            price = request.form.get("price")
            if total_leads is not None and price is not None:
                db.execute("UPDATE states SET total_leads=?, price=? WHERE id=?",
                           (int(total_leads or 0), int(price or 999), sid))
            elif total_leads is not None:
                db.execute("UPDATE states SET total_leads=? WHERE id=?",
                           (int(total_leads or 0), sid))
            elif price is not None:
                db.execute("UPDATE states SET price=? WHERE id=?",
                           (int(price or 999), sid))
            db.commit()
            if is_ajax:
                return jsonify({"ok": True, "message": "State leads & price updated successfully!"})
            flash("State leads & price updated successfully!")
            return redirect(url_for("admin_locations"))

        elif a in ("update_city", "price_city"):
            cid = request.form.get("id")
            total_leads = request.form.get("total_leads")
            price = request.form.get("price")
            if total_leads is not None and price is not None:
                db.execute("UPDATE cities SET total_leads=?, price=? WHERE id=?",
                           (int(total_leads or 0), int(price or 599), cid))
            elif total_leads is not None:
                db.execute("UPDATE cities SET total_leads=? WHERE id=?",
                           (int(total_leads or 0), cid))
            elif price is not None:
                db.execute("UPDATE cities SET price=? WHERE id=?",
                           (int(price or 599), cid))
            db.commit()
            if is_ajax:
                return jsonify({"ok": True, "message": "District leads & price updated successfully!"})
            flash("District leads & price updated successfully!")
            return redirect(url_for("admin_locations"))

        elif a == "add_cat" and request.form.get("name"):
            ensure_category(db, request.form["name"])
            db.commit()
            flash("Category added successfully!")
            return redirect(request.url)

        db.commit()
        return redirect(request.url)

    states = db.execute("""SELECT s.*, COUNT(c.id) as district_count
                           FROM states s LEFT JOIN cities c ON c.state_id=s.id
                           GROUP BY s.id ORDER BY s.name""").fetchall()
    cats = db.execute("SELECT * FROM categories").fetchall()
    cities = db.execute("""SELECT c.*, s.name as sname, s.slug as sslug FROM cities c
                           JOIN states s ON s.id=c.state_id
                           ORDER BY s.name, c.name""").fetchall()
    total_cities = len(cities)
    return render_template("admin/locations.html", states=states, cats=cats,
                           cities=cities, total_cities=total_cities)


@app.route("/admin/blog", methods=["GET", "POST"])
@admin_required
def admin_blog():
    db = get_db()
    if request.method == "POST":
        t = request.form["title"]
        db.execute("INSERT OR IGNORE INTO blog_posts(title,slug,excerpt,body) VALUES(?,?,?,?)",
                   (t, slugify(t), request.form["excerpt"], request.form["body"]))
        db.commit(); flash("Post published")
    posts = db.execute("SELECT * FROM blog_posts ORDER BY id DESC").fetchall()
    return render_template("admin/blog.html", posts=posts)


# ================= ADMIN SETTINGS (UPI, QR, WHATSAPP) =================
@app.route("/admin/settings", methods=["GET", "POST"])
@admin_required
def admin_settings():
    db = get_db()
    if request.method == "POST":
        action = request.form.get("action")

        # 1. Save UPI Details
        if action == "save_upi":
            upi_id = request.form.get("upi_id", "").strip()
            upi_name = request.form.get("upi_name", "").strip()
            if upi_id:
                set_setting("upi_id", upi_id)
            if upi_name:
                set_setting("upi_name", upi_name)
            flash("UPI ID & Beneficiary Name updated successfully! Changes reflect on all payment flows.")

        # 2. Save QR Code Settings
        elif action == "save_qr":
            if request.form.get("reset_qr"):
                set_setting("qr_mode", "dynamic")
                flash("QR Code reset to auto-generated dynamic mode!")
            else:
                qr_mode = request.form.get("qr_mode", "dynamic")
                set_setting("qr_mode", qr_mode)

                f = request.files.get("qr_image")
                if f and f.filename:
                    ext = os.path.splitext(f.filename)[1].lower()
                    if ext in (".png", ".jpg", ".jpeg", ".webp"):
                        qr_filename = "admin_custom_qr" + ext
                        qr_dir = os.path.join(BASE, "static", "qr")
                        os.makedirs(qr_dir, exist_ok=True)
                        save_path = os.path.join(qr_dir, qr_filename)
                        f.save(save_path)
                        set_setting("custom_qr_file", qr_filename)
                        set_setting("qr_mode", "custom")
                        flash("Custom QR / Bar Code uploaded and activated successfully!")
                    else:
                        flash("Invalid image format! Please upload PNG, JPG, or WebP.")
                else:
                    flash("QR Code mode settings saved successfully!")

        # 3. Save WhatsApp Details
        elif action == "save_whatsapp":
            wa_num = request.form.get("whatsapp_number", "").strip()
            wa_disp = request.form.get("whatsapp_display", "").strip()
            wa_msg = request.form.get("whatsapp_default_msg", "").strip()
            if wa_num:
                clean_num = re.sub(r"[^\d]", "", wa_num)
                set_setting("whatsapp_number", clean_num)
            if wa_disp:
                set_setting("whatsapp_display", wa_disp)
            if wa_msg:
                set_setting("whatsapp_default_msg", wa_msg)
            flash("WhatsApp settings updated! Chat redirect number and screen display text saved.")

        # 4. Save Calling Phone Number Details
        elif action == "save_call":
            c_num = request.form.get("call_number", "").strip()
            c_disp = request.form.get("call_display", "").strip()
            if c_num:
                set_setting("call_number", c_num)
            if c_disp:
                set_setting("call_display", c_disp)
            flash("Calling Phone Number updated! Reflects on direct phone call links and website call display.")

        # 5. Save Email Notification & SMTP Settings
        elif action == "save_smtp":
            smtp_host = request.form.get("smtp_host", "").strip()
            smtp_port = request.form.get("smtp_port", "").strip()
            smtp_user = request.form.get("smtp_user", "").strip()
            smtp_pass = request.form.get("smtp_pass", "").strip()
            smtp_from = request.form.get("smtp_from", "").strip()

            if smtp_host: set_setting("smtp_host", smtp_host)
            if smtp_port: set_setting("smtp_port", smtp_port)
            if smtp_user: set_setting("smtp_user", smtp_user)
            if smtp_pass: set_setting("smtp_pass", smtp_pass)
            if smtp_from: set_setting("smtp_from", smtp_from)

            if request.form.get("send_test"):
                send_sample_lead_email("test_applicant@example.com", "9876543210", request.remote_addr or "127.0.0.1")
                flash(f"Test lead email dispatched to {SAMPLE_NOTIFICATION_EMAIL}! Please check your inbox.")
            else:
                flash("Email Notification & SMTP settings saved successfully!")

        return redirect(url_for("admin_settings"))

    cfg = get_all_settings()
    return render_template("admin/settings.html", settings=cfg)

@app.route("/admin/settings/preview-qr")
@admin_required
def admin_preview_qr():
    cfg = get_all_settings()
    upi_id = cfg.get("upi_id", UPI_ID_DEFAULT)
    upi_name = cfg.get("upi_name", UPI_NAME_DEFAULT)
    import urllib.parse
    link = f"upi://pay?pa={upi_id}&pn={urllib.parse.quote(upi_name)}&am=5999&cu=INR&tn=Preview%20QR"
    img = qrcode.make(link)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return send_file(buf, mimetype="image/png")

init_db()
if __name__ == "__main__":
    app.run(debug=True)
