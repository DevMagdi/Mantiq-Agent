"""Reference data shared by bootstrap, seed, migrations and runtime (no credentials)."""
from __future__ import annotations

CURRENCIES = [
    ("EGP", "Egyptian Pound", "جنيه مصري"), ("SAR", "Saudi Riyal", "ريال سعودي"),
    ("AED", "UAE Dirham", "درهم إماراتي"), ("KWD", "Kuwaiti Dinar", "دينار كويتي"),
    ("QAR", "Qatari Riyal", "ريال قطري"), ("USD", "US Dollar", "دولار أمريكي"),
    ("EUR", "Euro", "يورو"), ("CNY", "Chinese Yuan", "يوان صيني"),
    ("TRY", "Turkish Lira", "ليرة تركية"), ("INR", "Indian Rupee", "روبية هندية"),
    ("GBP", "British Pound", "جنيه إسترليني"),
]

# UNSPSC-based subset covering the pilot's categories (steel, packaging, lubricants,
# spare parts, electrical, PPE, chemicals, MRO).
TAXONOMY = [
    {"code": "30000000", "name": "Structures and Building Components", "name_ar": "هياكل ومكونات إنشائية",
     "parent_code": None, "level": 1, "keywords": ["steel", "حديد", "صلب"], "default_uom": "t"},
    {"code": "30102400", "name": "Rebar / Reinforcing Steel", "name_ar": "حديد تسليح",
     "parent_code": "30000000", "level": 2,
     "keywords": ["rebar", "rebar 12mm", "reinforcing steel", "bbs", "حديد تسليح", "حديد 12", "سيخ"],
     "default_uom": "t"},
    {"code": "30102300", "name": "Steel Sections and Beams", "name_ar": "قطاعات وكمرات حديد",
     "parent_code": "30000000", "level": 2, "keywords": ["beam", "i-beam", "angle", "كمرة", "زاوية", "ماسورة"],
     "default_uom": "t"},
    {"code": "30111600", "name": "Cement and Mortar", "name_ar": "أسمنت ومونة",
     "parent_code": "30000000", "level": 2, "keywords": ["cement", "أسمنت", "اسمنت", "opc", "كلنكر", "مونة"],
     "default_uom": "t"},
    {"code": "31000000", "name": "Manufacturing Components and Supplies", "name_ar": "مكونات ومستلزمات تصنيع",
     "parent_code": None, "level": 1, "keywords": ["components", "spare", "قطع غيار"], "default_uom": "pcs"},
    {"code": "31171500", "name": "Bearings", "name_ar": "رولمان بلي",
     "parent_code": "31000000", "level": 2, "keywords": ["bearing", "bearings", "رولمان", "بلي", "skf"],
     "default_uom": "pcs"},
    {"code": "31201500", "name": "V-Belts and Pulleys", "name_ar": "سيور وبكرات",
     "parent_code": "31000000", "level": 2, "keywords": ["belt", "v-belt", "pulley", "سير", "بكرة"],
     "default_uom": "pcs"},
    {"code": "40151500", "name": "Industrial Pumps", "name_ar": "مضخات صناعية",
     "parent_code": None, "level": 2, "keywords": ["pump", "مضخة", "طلمبة", "centrifugal"], "default_uom": "pcs"},
    {"code": "24101600", "name": "Lifting Equipment", "name_ar": "معدات رفع",
     "parent_code": None, "level": 2, "keywords": ["hoist", "crane", "sling", "ونش", "رافعة", "سلك"],
     "default_uom": "pcs"},
    {"code": "24000000", "name": "Material Handling and Storage", "name_ar": "مناولة وتخزين",
     "parent_code": None, "level": 1, "keywords": ["pallet", "racking", "بالتة", "رفوف"], "default_uom": "pcs"},
    {"code": "24112000", "name": "Packaging Materials", "name_ar": "مواد تعبئة وتغليف",
     "parent_code": "24000000", "level": 2,
     "keywords": ["packaging", "stretch film", "carton", "shrink", "تغليف", "كرتون", "استرتش", "شرنك"],
     "default_uom": "roll"},
    {"code": "24121700", "name": "Woven Bags and Sacks", "name_ar": "شكائر وأكياس منسوجة",
     "parent_code": "24000000", "level": 2, "keywords": ["bag", "pp bag", "sack", "شيكارة", "كيس", "خيش"],
     "default_uom": "pcs"},
    {"code": "12180000", "name": "Lubricants and Greases", "name_ar": "زيوت وشحوم",
     "parent_code": None, "level": 2,
     "keywords": ["lubricant", "grease", "oil", "gear oil", "زيت", "شحم", "تزييت"], "default_uom": "l" },
    {"code": "15120000", "name": "Fuels", "name_ar": "وقود",
     "parent_code": None, "level": 2, "keywords": ["diesel", "fuel", "سولار", "ديزل", "بنزين"], "default_uom": "l"},
    {"code": "39000000", "name": "Electrical Systems and Lighting", "name_ar": "أنظمة كهربائية وإضاءة",
     "parent_code": None, "level": 1, "keywords": ["electrical", "كهرباء", "إضاءة"], "default_uom": "pcs"},
    {"code": "39121600", "name": "Circuit Breakers and Protection", "name_ar": "قواطع وحماية",
     "parent_code": "39000000", "level": 2,
     "keywords": ["breaker", "mcb", "contactor", "relay", "قاطع", "كونتاكتور", "اوفرلود", "ريلاي"],
     "default_uom": "pcs"},
    {"code": "39112000", "name": "Cables and Wiring", "name_ar": "كابلات وأسلاك",
     "parent_code": "39000000", "level": 2, "keywords": ["cable", "wire", "كابل", "سلك", "nyy"], "default_uom": "m"},
    {"code": "39131500", "name": "Industrial Lighting", "name_ar": "إضاءة صناعية",
     "parent_code": "39000000", "level": 2, "keywords": ["lamp", "led", "light", "لمبة", "ليد", "كشاف"],
     "default_uom": "pcs"},
    {"code": "47000000", "name": "Cleaning and Hygiene", "name_ar": "تنظيف ونظافة",
     "parent_code": None, "level": 1, "keywords": ["cleaning", "hygiene", "تنظيف"], "default_uom": "l"},
    {"code": "53131600", "name": "Personal Protective Equipment", "name_ar": "معدات حماية شخصية",
     "parent_code": None, "level": 2,
     "keywords": ["ppe", "helmet", "gloves", "safety shoes", "خوذة", "قفاز", "حماية", "جزمة سلامة"],
     "default_uom": "pcs"},
    {"code": "12352100", "name": "Industrial Chemicals", "name_ar": "كيماويات صناعية",
     "parent_code": None, "level": 2,
     "keywords": ["chemical", "caustic soda", "acid", "resin", "كيماويات", "صودا", "حمض", "راتنج"],
     "default_uom": "kg"},
    {"code": "27000000", "name": "Tools and General Machinery", "name_ar": "عدد وآلات عامة",
     "parent_code": None, "level": 1, "keywords": ["tools", "عدد", "أدوات"], "default_uom": "pcs"},
    {"code": "27112100", "name": "Hand Tools", "name_ar": "عدد يدوية",
     "parent_code": "27000000", "level": 2, "keywords": ["wrench", "drill", "hand tool", "مفتاح", "شاكوش"],
     "default_uom": "pcs"},
    {"code": "40141700", "name": "Valves and Fittings", "name_ar": "محابس ووصلات",
     "parent_code": None, "level": 2, "keywords": ["valve", "fitting", "flange", "محبس", "فلانشة", "كوع"],
     "default_uom": "pcs"},
]

UNIT_CONVERSIONS = [
    ("t", "kg", "1000", "mass"), ("kg", "t", "0.001", "mass"), ("lb", "kg", "0.45359237", "mass"),
    ("kg", "lb", "2.20462262", "mass"), ("t", "lb", "2204.62262", "mass"),
    ("m", "ft", "3.280839895", "length"), ("ft", "m", "0.3048", "length"),
    ("m", "cm", "100", "length"), ("cm", "mm", "10", "length"),
    ("l", "ml", "1000", "volume"), ("m3", "l", "1000", "volume"),
    ("doz", "pcs", "12", "count"), ("pcs", "doz", "0.0833333333", "count"),
    ("bag", "pcs", "1", "count"), ("roll", "pcs", "1", "count"), ("drum", "l", "208", "volume"),
]

CATEGORIES = [
    {"code": "STEEL", "name": "Steel & Rebar", "name_ar": "حديد وصلب", "taxonomy_code": "30102400",
     "weights": {"price": 0.45, "lead_time": 0.2, "quality": 0.2, "terms": 0.05, "risk": 0.1}},
    {"code": "PACKAGING", "name": "Packaging", "name_ar": "تغليف", "taxonomy_code": "24112000",
     "weights": {"price": 0.4, "lead_time": 0.25, "quality": 0.2, "terms": 0.05, "risk": 0.1}},
    {"code": "LUBRICANTS", "name": "Lubricants", "name_ar": "زيوت وشحوم", "taxonomy_code": "12180000",
     "weights": {"price": 0.45, "lead_time": 0.2, "quality": 0.2, "terms": 0.05, "risk": 0.1}},
    {"code": "SPARE_PARTS", "name": "Spare Parts", "name_ar": "قطع غيار", "taxonomy_code": "31171500",
     "weights": {"price": 0.3, "lead_time": 0.25, "quality": 0.25, "terms": 0.05, "risk": 0.15}},
    {"code": "ELECTRICAL", "name": "Electrical", "name_ar": "كهرباء", "taxonomy_code": "39121600",
     "weights": {"price": 0.35, "lead_time": 0.2, "quality": 0.25, "terms": 0.05, "risk": 0.15}},
    {"code": "MRO", "name": "MRO & General", "name_ar": "مستلزمات عامة", "taxonomy_code": "27000000",
     "weights": {"price": 0.4, "lead_time": 0.2, "quality": 0.2, "terms": 0.1, "risk": 0.1}},
]

PLANS = [
    {"code": "starter", "name": "Starter", "price_monthly": "490", "currency": "USD",
     "limits": {"users": 5, "rfqs_per_month": 30, "messages_per_month": 500, "llm_tokens_per_month": 2_000_000,
                "voice_minutes_per_month": 0},
     "features": ["email", "web_form", "1 plant"]},
    {"code": "growth", "name": "Growth", "price_monthly": "1490", "currency": "USD",
     "limits": {"users": 25, "rfqs_per_month": 200, "messages_per_month": 5000,
                "llm_tokens_per_month": 15_000_000, "voice_minutes_per_month": 200},
     "features": ["email", "whatsapp", "web_form", "voice", "discovery", "split_award", "5 plants"]},
    {"code": "enterprise", "name": "Enterprise", "price_monthly": "3900", "currency": "USD",
     "limits": {"users": 200, "rfqs_per_month": 2000, "messages_per_month": 50000,
                "llm_tokens_per_month": 100_000_000, "voice_minutes_per_month": 2000},
     "features": ["all channels", "SSO", "self-hosted LLM", "on-premise", "unlimited plants", "SLA"]},
]

DEFAULT_TEMPLATES = [
    {
        "code": "rfq_invitation", "channel": "email", "locale": "ar", "name": "دعوة لطلب عرض سعر",
        "subject": "طلب عرض سعر {{rfq_code}} - {{title}}",
        "body": (
            "السيد/{{supplier_name}} المحترم،\n\n"
            "تتشرف شركة {{tenant_name}} بدعوتكم لتقديم عرض سعر لـ: {{title}}.\n"
            "رقم الطلب: {{rfq_code}}\n"
            "الكمية: {{quantity_summary}}\n"
            "موعد الإغلاق: {{deadline}}\n"
            "شروط التسليم: {{incoterm}} - {{delivery_location}}\n"
            "شروط السداد: {{payment_terms}}\n\n"
            "يمكنكم تقديم العرض عبر الرابط التالي دون الحاجة إلى حساب:\n{{web_form_url}}\n\n"
            "مع خالص التقدير،\n{{buyer_name}}\n{{tenant_name}}"
        ),
        "whatsapp_template_name": "",
    },
    {
        "code": "rfq_invitation", "channel": "email", "locale": "en", "name": "RFQ invitation",
        "subject": "Request for Quotation {{rfq_code}} - {{title}}",
        "body": (
            "Dear {{supplier_name}},\n\n"
            "{{tenant_name}} invites you to quote for: {{title}}.\n"
            "RFQ: {{rfq_code}}\nQuantity: {{quantity_summary}}\nDeadline: {{deadline}}\n"
            "Delivery: {{incoterm}} - {{delivery_location}}\nPayment terms: {{payment_terms}}\n\n"
            "Submit your offer through this secure link (no account needed):\n{{web_form_url}}\n\n"
            "Best regards,\n{{buyer_name}}\n{{tenant_name}}"
        ),
        "whatsapp_template_name": "",
    },
    {
        "code": "rfq_invitation", "channel": "whatsapp", "locale": "ar", "name": "دعوة واتساب",
        "subject": "", "body": (
            "مرحباً {{supplier_name}}، {{tenant_name}} تطلب عرض سعر لـ {{title}} ({{rfq_code}}). "
            "الموعد النهائي {{deadline}}. قدّم عرضك: {{web_form_url}}"
        ),
        "whatsapp_template_name": "rfq_invitation_ar",
    },
    {
        "code": "reminder", "channel": "email", "locale": "ar", "name": "تذكير",
        "subject": "تذكير: عرض السعر {{rfq_code}}",
        "body": (
            "السيد/{{supplier_name}}،\nنود التذكير بأن موعد تقديم عرض السعر للطلب {{rfq_code}} هو {{deadline}}.\n"
            "نرجو إرسال عرضكم عبر: {{web_form_url}}\n\nشكراً،\n{{tenant_name}}"
        ),
    },
    {
        "code": "missing_info", "channel": "email", "locale": "ar", "name": "طلب معلومات ناقصة",
        "subject": "استكمال عرض السعر {{rfq_code}}",
        "body": (
            "السيد/{{supplier_name}},\nشكراً لعرضكم. لتتمكن من المقارنة، نحتاج المعلومات التالية:\n{{missing_fields}}\n"
            "مع التقدير،\n{{tenant_name}}"
        ),
    },
    {
        "code": "counter_offer", "channel": "email", "locale": "ar", "name": "عرض مقابل",
        "subject": "عرض مقابل - {{rfq_code}}",
        "body": "السيد/{{supplier_name}},\n{{message_body}}\nمع التقدير،\n{{buyer_name}}\n{{tenant_name}}",
    },
]

COUNTRY_CALL_POLICIES = [
    {"country": "EG", "allowed_hours_start": 9, "allowed_hours_end": 17,
     "recording_consent_mode": "explicit",
     "disclosure_text_en": "Hello, this is an AI assistant calling on behalf of the buyer to collect a quotation. This call may be recorded if you agree.",
     "disclosure_text_ar": "أهلاً بحضرتك، أنا مساعد ذكاء اصطناعي بسم الله الرحمن الرحيم بتكلم نيابة عن جهة الشراء لجمع عرض سعر. المكالمة قد يتم تسجيلها لو حضرتك موافق.",
     "max_call_seconds": 480},
    {"country": "SA", "allowed_hours_start": 9, "allowed_hours_end": 17,
     "recording_consent_mode": "explicit",
     "disclosure_text_en": "Hello, this is an AI assistant calling on behalf of the buyer. Do you consent to this call being recorded?",
     "disclosure_text_ar": "مرحباً، أنا مساعد ذكاء اصطناعي أتصل نيابة عن جهة الشراء. هل توافق على تسجيل المكالمة؟",
     "max_call_seconds": 420},
    {"country": "AE", "allowed_hours_start": 9, "allowed_hours_end": 18,
     "recording_consent_mode": "explicit",
     "disclosure_text_en": "Hello, this is an AI assistant calling on behalf of the buyer. Do you consent to recording?",
     "disclosure_text_ar": "مرحباً، أنا مساعد ذكاء اصطناعي أتصل نيابة عن جهة الشراء. هل توافق على التسجيل؟",
     "max_call_seconds": 420},
]

# Duty / VAT profiles used for landed cost (FR-RES-4). Configurable by Admin.
DUTY_PROFILES = [
    {"country": "EG", "vat_rate": "0.14", "duty_rate_default": "0.05",
     "duty_rates": {"STEEL": "0.10", "PACKAGING": "0.20", "LUBRICANTS": "0.30", "SPARE_PARTS": "0.05",
                    "ELECTRICAL": "0.10", "MRO": "0.15"}},
    {"country": "SA", "vat_rate": "0.15", "duty_rate_default": "0.05",
     "duty_rates": {"STEEL": "0.05", "PACKAGING": "0.10", "LUBRICANTS": "0.20", "SPARE_PARTS": "0.05",
                    "ELECTRICAL": "0.08", "MRO": "0.10"}},
    {"country": "AE", "vat_rate": "0.05", "duty_rate_default": "0.05", "duty_rates": {}},
]

DEMO_SUPPLIERS = [
    {"name": "Ezz Steel Distribution", "name_ar": "عز للصلب - التوزيع", "country": "EG", "city": "Cairo",
     "website": "https://ezzsteel.example.com", "category": "STEEL", "score": 88,
     "email": "sales@ezzsteel.example.com", "phone": "+20 100 111 2233", "language": "ar"},
    {"name": "Al-Marakby Steel Trading", "name_ar": "المراكبي لتجارة حديد التسليح", "country": "EG", "city": "Giza",
     "website": "https://almarakby.example.com", "category": "STEEL", "score": 82,
     "email": "offers@almarakby.example.com", "phone": "+20 122 334 5566", "language": "ar"},
    {"name": "Suez Metal Works", "name_ar": "مصنع السويس للمعادن", "country": "EG", "city": "Suez",
     "website": "https://suezmetal.example.com", "category": "STEEL", "score": 76,
     "email": "rfq@suezmetal.example.com", "phone": "+20 106 778 9911", "language": "en"},
    {"name": "Gulf Steel Co.", "name_ar": "شركة الخليج للحديد", "country": "SA", "city": "Dammam",
     "website": "https://gulfsteel.example.com", "category": "STEEL", "score": 79,
     "email": "sales@gulfsteel.example.com", "phone": "+966 50 222 3344", "language": "ar"},
    {"name": "Cairo Packaging Group", "name_ar": "مجموعة القاهرة للتعبئة", "country": "EG", "city": "Cairo",
     "website": "https://cairopack.example.com", "category": "PACKAGING", "score": 84,
     "email": "sales@cairopack.example.com", "phone": "+20 100 555 6677", "language": "ar"},
    {"name": "Delta Plastics", "name_ar": "دلتا للبلاستيك", "country": "EG", "city": "Tanta",
     "website": "https://deltaplast.example.com", "category": "PACKAGING", "score": 74,
     "email": "info@deltaplast.example.com", "phone": "+20 111 222 3344", "language": "ar"},
    {"name": "PetroLub Egypt", "name_ar": "بترولوب مصر", "country": "EG", "city": "Alexandria",
     "website": "https://petrolub.example.com", "category": "LUBRICANTS", "score": 90,
     "email": "b2b@petrolub.example.com", "phone": "+20 128 444 5566", "language": "ar"},
    {"name": "Lubricants House", "name_ar": "بيت الزيوت", "country": "EG", "city": "Cairo",
     "website": "https://lubhouse.example.com", "category": "LUBRICANTS", "score": 71,
     "email": "quotes@lubhouse.example.com", "phone": "+20 102 333 4455", "language": "ar"},
    {"name": "Nile Bearings", "name_ar": "النيل للرولمان بلي", "country": "EG", "city": "Cairo",
     "website": "https://nilebearing.example.com", "category": "SPARE_PARTS", "score": 86,
     "email": "sales@nilebearing.example.com", "phone": "+20 100 999 0011", "language": "ar"},
    {"name": "Egypt Electrical Supply", "name_ar": "مصر للتوريدات الكهربائية", "country": "EG", "city": "Cairo",
     "website": "https://eegsupply.example.com", "category": "ELECTRICAL", "score": 83,
     "email": "rfq@eegsupply.example.com", "phone": "+20 109 777 8899", "language": "ar"},
    {"name": "Saudi Electrical Co.", "name_ar": "الشركة السعودية للكهرباء", "country": "SA", "city": "Riyadh",
     "website": "https://saudielectric.example.com", "category": "ELECTRICAL", "score": 77,
     "email": "sales@saudielectric.example.com", "phone": "+966 55 123 4567", "language": "ar"},
    {"name": "Alexandria Industrial Supplies", "name_ar": "الإسكندرية للتوريدات الصناعية", "country": "EG",
     "city": "Alexandria", "website": "https://alexind.example.com", "category": "MRO", "score": 80,
     "email": "info@alexind.example.com", "phone": "+20 103 222 1100", "language": "ar"},
]

DEMO_PLANTS = [
    {"code": "PLT-SADAT", "name": "Sadat City Plant", "name_ar": "مصنع مدينة السادات", "city": "Sadat City",
     "address": "Industrial Zone 2, Sadat City"},
    {"code": "PLT-6OCT", "name": "6th of October Plant", "name_ar": "مصنع السادس من أكتوبر", "city": "6th of October",
     "address": "Industrial Zone 4, 6th of October"},
    {"code": "PLT-ALEX", "name": "Alexandria Plant", "name_ar": "مصنع الإسكندرية", "city": "Alexandria",
     "address": "Amreya Industrial Zone"},
]


COLUMN_SYNONYMS: dict[str, list[str]] = {
    "description": ["item", "description", "material", "product", "البيان", "الوصف", "الصنف", "الخامة", "المادة"],
    "quantity": ["quantity", "qty", "amount", "الكمية", "العدد", "الكميه"],
    "uom": ["unit", "uom", "unit of measure", "الوحدة", "وحده القياس", "الوحده"],
    "needed_by": ["required date", "needed by", "delivery date", "تاريخ التسليم", "تاريخ الاحتياج", "الموعد"],
    "plant": ["plant", "site", "location", "المصنع", "الموقع", "الفرع"],
    "spec": ["spec", "specification", "grade", "المواصفات", "الدرجة", "المقاس"],
    "target_price": ["target price", "budget", "price", "السعر المستهدف", "الميزانية", "السعر"],
    "currency": ["currency", "العملة"],
    "sku": ["sku", "code", "item code", "الكود", "كود الصنف"],
}
