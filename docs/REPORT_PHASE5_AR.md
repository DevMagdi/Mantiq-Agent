<div dir="rtl">

# تقرير المرحلة 5 — شاشة التكاملات التي تُهيّئ المنتج فعلاً

**الفرع:** `arena/cc56b821-mantiq-agent` · **الدمج:** PR #10 → `4c2026f` على `main`
**تاريخ التنفيذ:** 2026-10-31

## 1. المشكلة التي كانت قائمة

تبويب «التكاملات» كان ديكورًا: يكتب صفًّا في قاعدة البيانات لا يقرأه أي كود، فيمكن للمستأجر أن
«يهيّئ» SMTP ويبقى النظام يرسل بالـ fake adapter. المطلوب في هذه المرحلة كان أن تصبح الشاشة هي
مركز التحكم الحقيقي، وأن يثبت ذلك بالدليل لا بالكلام.

## 2. ما أنجزناه

| القدرة المطلوبة | الحالة | الدليل |
| --- | --- | --- |
| عرض حالة كل مزوّد بوضوح (`Live` / `Sandbox` / `Not configured` / `Error` / `Disabled`) | ✅ | حالة محسوبة عند كل قراءة في `snapshot()`؛ 8 اختبارات واجهة |
| إدخال بيانات اعتماد حقيقية في حقول منظّمة لكل مزوّد (لا صندوق JSON فارغ) | ✅ | كتالوج في الخلفية ترسمه الواجهة: `llm`، `whatsapp`، `sms`، `telephony`، `email`، `search` |
| زر **Test connection** بنتيجة قاطعة | ✅ | نداء حقيقي واحد إلى المزوّد + زمن الاستجابة؛ الـ fake لا يُعتبر نجاحًا أبدًا |
| تفعيل / تعطيل مزوّد | ✅ | `PATCH /integrations/{kind}` يعيد المسار إلى منصة افتراضية آمنة |
| رسالة نجاح/خطأ بعد كل إجراء + بقاء البيانات بعد إعادة التحميل | ✅ | رحلة e2e تعيد التحميل وتتحقق من بقاء القناع والحالة |

تفاصيل التنفيذ في `docs/PR_PHASE5_INTEGRATIONS.md` (بالإنجليزية، مع خريطة للمراجع)، والقرارات
المعمارية 114–118 في `docs/DECISIONS.md`.

## 3. قرارات جوهرية

1. **بيانات الاعتماد للمستأجر تُخزَّن مشفّرة في قاعدة البيانات** بمفتاح المستأجر (`EncryptedString`)،
   لا في متغيّرات البيئة: المستأجر لا يحتاج نشرًا جديدًا كي يصبح حيًّا.
2. **الأولوية:** تكامل المستأجر (كامل **و** مفعّل) → الصف القديم `LlmProviderConfig` (لـ LLM فقط) →
   الافتراضي من البيئة. التعطيل يُخرج الصف من السلسلة، وهذا هو «الوضع الآمن» بلا استثناءات.
3. **الأسرار تُكتب ولا تُقرأ:** الـ API يعيد أسماء الحقول والقناع `••••` + آخر أربعة أحرف فقط،
   وترك الحقل فارغًا عند الحفظ التالي يُبقي السر المخزَّن. كل تغيير يُكتب في سجل التدقيق بالأسماء لا
   بالقيم.
4. **الإثبات يبدأ من الشاشة:** في اختبار e2e تبقى المنصة على `fake` ولا يوجد أي سرّ للمزوّد في
   البيئة، فأي حالة `live` أو نداء حقيقي لا يمكن أن يأتي إلا من بيانات اعتماد أُدخلت عبر الشاشة.
5. **التوثيق صار اختبارًا:** `tests/test_env_docs.py` يُفشل البناء إذا وُثّق متغيّر غير موجود أو بقي
   متغيّر في `Settings` بلا توثيق أو ظهر ما يشبه سرًّا حقيقيًا في القالب.

## 4. نتائج الاختبارات الفعلية (كما نُفِّذت)

### 4.1 الخلفية

```console
$ cd backend && ruff check app tests ../scripts/e2e_journeys.py
All checks passed!

$ alembic upgrade head        # 0016_channels_voice -> 0017_tenant_integrations
$ alembic check
No new upgrade operations detected.

$ python -m pytest tests -q --cov=app
junit: {'tests': '746', 'failures': '0', 'errors': '0', 'skipped': '0', 'time': '98.097'}
TOTAL                               12412   1543    88%
```

### 4.2 الواجهة

```console
$ npm run typecheck      # exit 0
$ npm run lint           # exit 0 (eslint . --max-warnings 0)
$ npm run test           # Test Files 14 passed (14) / Tests 84 passed (84)
$ npm run e2e:typecheck  # exit 0
$ npm run build          # ✓ built in 6.99s
```

### 4.3 اختبارات الشاشة نفسها

```console
$ npx vitest run src/components/integrations.test.tsx
 ✓ يرسم حقول الكتالوج بدل صندوق JSON فارغ
 ✓ يعرض الحالات الأربع الصادقة ومصدر كل تهيئة
 ✓ يُقنِّع السر المخزَّن ولا يعرض القيمة الخام أبدًا
 ✓ يحفظ بيانات اعتماد حقيقية عبر الـ API ويعلن الحالة الجديدة
 ✓ يختبر الاتصال ويعرض رد المزوّد وزمن الاستجابة
 ✓ يعطّل التكامل ويعيد المسار إلى المنصة
 ✓ يعرض المحوّلات التي تديرها المنصة للقراءة فقط
 ✓ يتحدث العربية بالحقول نفسها عند تغيير اللغة
 Test Files  1 passed (1) / Tests  8 passed (8)
```

### 4.4 الرحلات الشاملة (نسختان حقيقيتان + Redis حقيقي)

```console
$ python scripts/e2e_journeys.py
  PASS  replicas — two API processes share Redis at 127.0.0.1:56185/0
  PASS  sign in · token refresh · sign out
  PASS  websocket — a live socket is open on each replica
  PASS  integrations — fake → live from the screen: llm=live, whatsapp=live, platform still fake
  PASS  requisition · RFQ · WhatsApp outbound · inbound WhatsApp (401 without a signature)
  PASS  live event fan-out — offer.extracted reached both replicas' sockets
  PASS  LLM extraction — a supplier reply was read by the real provider
  PASS  capabilities · feature flag
  PASS  integration safety — disabling falls back without calling the vendor; re-enabling restores it

15 journeys passed in 11.1s
```

### 4.5 التكامل المستمر

PR #10: ست وظائف CI كلها ناجحة (خلفية، واجهة، PostgreSQL 16 مع RLS وفحص الهجرات، فحص الأسرار
والتبعيات، Helm، صور الحاويات). ثم دُمج إلى `main` في `4c2026f`.

## 5. ما لم يُنجز — بصراحة

1. **لم نتصل بأي حساب مزوّد حقيقي.** هذه البيئة لا تصل إلى `api.anthropic.com` ولا `api.twilio.com`
   ولا `graph.facebook.com` ولا `serpapi.com` ولا أي مرحّل SMTP، ولا توجد مفاتيح. كل نداء «حقيقي»
   في هذا التقرير ذهب إلى خادم محلي يتحدث صيغة المزوّد الموثقة، مع تنفيذ كودنا الحقيقي بالكامل
   (الترويسات، المصادقة، إعادة المحاولة، تحويل الأخطاء، التحليل). لتشغيل تحقق على حساباتكم: أدخلوا
   مفتاحًا في الحقل واضغطوا **Test connection** — هذا هو الغرض من الشاشة. أرسلوا مفتاح اختبار وأعيد
   التشغيل.
2. **لا لقطة شاشة.** تنزيل متصفح Playwright محجوب هنا (`cdn.playwright.dev`)، فالشاشة مُثبتة بـ 8
   اختبارات مكوّنات + `frontend/e2e/integrations.spec.ts` (مكتوب ومُفحص نوعيًا في CI، ولم يُنفَّذ).
   لا يوجد في هذا التقرير أي ادعاء بتشغيل متصفح.
3. **بوابة الإقلاع ما زالت تعتمد البيئة:** العملية تبدأ قبل أن تعرف المستأجرين، لذا يحتاج النشر
   الصارم إلى `EMAIL_PROVIDER` و`LLM_PROVIDER` حقيقيين ولو جلب المستأجرون مفاتيحهم لاحقًا. موثّق في
   `docs/ENVIRONMENT.md` §2.1.

## 6. القواعد غير القابلة للكسر — ما زالت سليمة

- النموذج يقترح فقط؛ محرّك السياسات والإنسان يقرّران. لا قبول آلي، لا توقيع، لا تنفيذ دفع، لا تغيير
  مواصفة. اختبارات الأدوات المحظورة (`can_act: false`) تمر.
- محتوى المورّد غير موثوق، ولا أسرار ولا بيانات شخصية كاملة في السجلات.
- لا سرّ واحد في المستودع: فحص الأسرار في CI ناجح، و`tests/test_env_docs.py` يمنع قالبًا يشبه
  الاعتماد الحقيقي.
- واجهة ثنائية اللغة: العربية RTL هي الافتراضية، وكل نص جديد في الشاشة موجود بالعربية والإنجليزية.

## 7. كيف تتحقق بنفسك

```bash
cd backend && pytest tests/test_api_integrations.py -q     # 15
cd frontend && npm run test                                # 84
python scripts/e2e_journeys.py                             # 15 رحلة
```

يدويًا: تسجيل الدخول كمدير مستأجر → **الإعدادات ← التكاملات** → «نموذج اللغة» → Anthropic →
ألصق المفتاح → **حفظ** (تصبح `Live` بعد نجاح الفحص) → **اختبار الاتصال** → أعد التحميل (المفتاح
`••••abcd`) → **تعطيل** (تصبح `Disabled` ويعود الاستخراج للقارئ الحتمي).

</div>
