"""Translations for the RainDeals home screen and language picker.

Only the home screen copy and its button captions are translated here. Product
names, seller content, and transactional instructions stay as authored.
"""

from __future__ import annotations


# Each row is: code, picker label, products, featured, orders, freebies,
# profile, deposit, bot API, store, language, referrals, supplier, channel,
# welcome heading, short intro, prompt.
_ROWS = [
    ("en", "🇬🇧 English", "Products", "Featured product", "My Orders", "Freebies", "Profile", "Deposit", "Bot API", "Store", "Language", "Refer & Earn", "Become a Supplier", "Join our channel", "Welcome to RainDeals", "Get instant access to premium AI tools with fully automatic delivery.", "Please choose an option below to continue."),
    ("ur", "🇵🇰 اردو (Pakistan)", "مصنوعات", "نمایاں پروڈکٹ", "میرے آرڈرز", "مفت تحائف", "پروفائل", "رقم جمع", "بوٹ API", "اسٹور", "زبان", "دوستوں کو بلائیں اور کمائیں", "سپلائر بنیں", "ہمارا چینل جوائن کریں", "RainDeals میں خوش آمدید", "مکمل خودکار ڈیلیوری کے ساتھ پریمیم AI ٹولز تک فوری رسائی حاصل کریں۔", "جاری رکھنے کے لیے نیچے ایک آپشن منتخب کریں۔"),
    ("hi", "🇮🇳 हिन्दी (India)", "प्रोडक्ट्स", "विशेष प्रोडक्ट", "मेरे ऑर्डर", "मुफ़्त उपहार", "प्रोफ़ाइल", "डिपॉज़िट", "बॉट API", "स्टोर", "भाषा", "रेफ़र करें और कमाएँ", "सप्लायर बनें", "हमारा चैनल जॉइन करें", "RainDeals में आपका स्वागत है", "पूरी तरह ऑटोमैटिक डिलीवरी के साथ प्रीमियम AI टूल्स तुरंत पाएँ।", "जारी रखने के लिए नीचे एक विकल्प चुनें।"),
    ("bn", "🇧🇩 বাংলা (Bangladesh)", "পণ্যসমূহ", "বিশেষ পণ্য", "আমার অর্ডার", "বিনামূল্যের উপহার", "প্রোফাইল", "ডিপোজিট", "বট API", "স্টোর", "ভাষা", "রেফার করুন ও আয় করুন", "সরবরাহকারী হন", "আমাদের চ্যানেলে যোগ দিন", "RainDeals-এ স্বাগতম", "সম্পূর্ণ স্বয়ংক্রিয় ডেলিভারিতে প্রিমিয়াম AI টুলস দ্রুত পান।", "চালিয়ে যেতে নিচের একটি বিকল্প বেছে নিন।"),
    ("ar", "🇸🇦 العربية (Arabia)", "المنتجات", "منتج مميز", "طلباتي", "هدايا مجانية", "الملف الشخصي", "إيداع", "واجهة البوت API", "المتجر", "اللغة", "ادعُ واربح", "كن مورّدًا", "انضم إلى قناتنا", "مرحبًا بك في RainDeals", "احصل فورًا على أدوات AI المميزة مع تسليم آلي بالكامل.", "اختر أحد الخيارات أدناه للمتابعة."),
    ("fa", "🇮🇷 فارسی (Iran)", "محصولات", "محصول ویژه", "سفارش‌های من", "هدایای رایگان", "پروفایل", "واریز", "API ربات", "فروشگاه", "زبان", "دعوت کن و درآمد داشته باش", "تأمین‌کننده شوید", "به کانال ما بپیوندید", "به RainDeals خوش آمدید", "با تحویل کاملاً خودکار، فوراً به ابزارهای پریمیوم هوش مصنوعی دسترسی پیدا کنید.", "برای ادامه یکی از گزینه‌های زیر را انتخاب کنید."),
    ("ps", "🇦🇫 پښتو (Afghanistan)", "محصولات", "ځانګړی محصول", "زما سپارښتنې", "وړیا ډالۍ", "پروفایل", "زیرمه", "د بوټ API", "پلورنځی", "ژبه", "ملګري راوبلئ او عاید ترلاسه کړئ", "عرضه کوونکی شئ", "زموږ چینل سره یوځای شئ", "RainDeals ته ښه راغلاست", "د بشپړ اتومات سپارلو سره پریمیم AI وسایلو ته سمدستي لاسرسی ومومئ.", "د دوام لپاره لاندې یو انتخاب وکړئ."),
    ("tr", "🇹🇷 Türkçe (Türkiye)", "Ürünler", "Öne çıkan ürün", "Siparişlerim", "Ücretsiz hediyeler", "Profil", "Bakiye yükle", "Bot API", "Mağaza", "Dil", "Davet et ve kazan", "Tedarikçi ol", "Kanalımıza katıl", "RainDeals'e hoş geldiniz", "Tam otomatik teslimatla premium AI araçlarına anında erişin.", "Devam etmek için aşağıdan bir seçenek belirleyin."),
    ("zh", "🇨🇳 中文 (China)", "商品", "精选商品", "我的订单", "免费赠品", "个人资料", "充值", "机器人 API", "商店", "语言", "邀请好友赚奖励", "成为供应商", "加入我们的频道", "欢迎来到 RainDeals", "尊享高级 AI 工具，商品自动交付，即刻使用。", "请选择下方选项以继续。"),
    ("ja", "🇯🇵 日本語 (Japan)", "商品", "おすすめ商品", "注文履歴", "無料特典", "プロフィール", "入金", "Bot API", "ストア", "言語", "紹介して報酬を獲得", "サプライヤーになる", "公式チャンネルに参加", "RainDealsへようこそ", "プレミアムAIツールを自動配信で今すぐ利用できます。", "続行するには、下の項目を選択してください。"),
    ("ko", "🇰🇷 한국어 (Korea)", "상품", "추천 상품", "내 주문", "무료 혜택", "프로필", "충전", "봇 API", "스토어", "언어", "친구 초대하고 적립", "공급업체 신청", "공식 채널 참여", "RainDeals에 오신 것을 환영합니다", "프리미엄 AI 도구를 자동 배송으로 바로 이용하세요.", "계속하려면 아래 옵션을 선택하세요."),
    ("mn", "🇲🇳 Монгол (Mongolia)", "Бүтээгдэхүүн", "Онцлох бүтээгдэхүүн", "Миний захиалга", "Үнэгүй бэлэг", "Профайл", "Данс цэнэглэх", "Бот API", "Дэлгүүр", "Хэл", "Найзаа урьж урамшуулал авах", "Нийлүүлэгч болох", "Манай сувагт нэгдэх", "RainDeals-д тавтай морил", "Дээд зэрэглэлийн AI хэрэгслийг автомат хүргэлтээр шууд аваарай.", "Үргэлжлүүлэхийн тулд доорх сонголтоос сонгоно уу."),
    ("id", "🇮🇩 Indonesia", "Produk", "Produk unggulan", "Pesanan Saya", "Gratisan", "Profil", "Deposit", "Bot API", "Toko", "Bahasa", "Ajak teman & dapatkan hadiah", "Jadi Pemasok", "Gabung kanal kami", "Selamat datang di RainDeals", "Akses alat AI premium dengan pengiriman otomatis instan.", "Pilih salah satu opsi di bawah untuk melanjutkan."),
    ("ms", "🇲🇾 Melayu (Malaysia)", "Produk", "Produk pilihan", "Pesanan Saya", "Hadiah percuma", "Profil", "Deposit", "Bot API", "Kedai", "Bahasa", "Jemput rakan & dapat ganjaran", "Jadi Pembekal", "Sertai saluran kami", "Selamat datang ke RainDeals", "Dapatkan akses segera kepada alat AI premium dengan penghantaran automatik.", "Pilih satu pilihan di bawah untuk meneruskan."),
    ("fil", "🇵🇭 Filipino (Philippines)", "Mga Produkto", "Tampok na Produkto", "Mga Order Ko", "Mga Libreng Alok", "Profile", "Magdeposito", "Bot API", "Tindahan", "Wika", "Mag-refer at kumita", "Maging Supplier", "Sumali sa aming channel", "Maligayang pagdating sa RainDeals", "Makakuha agad ng premium AI tools na may awtomatikong delivery.", "Pumili ng opsyon sa ibaba para magpatuloy."),
    ("vi", "🇻🇳 Tiếng Việt (Vietnam)", "Sản phẩm", "Sản phẩm nổi bật", "Đơn hàng của tôi", "Quà tặng miễn phí", "Hồ sơ", "Nạp tiền", "Bot API", "Cửa hàng", "Ngôn ngữ", "Giới thiệu bạn bè để nhận thưởng", "Trở thành nhà cung cấp", "Tham gia kênh của chúng tôi", "Chào mừng đến với RainDeals", "Truy cập ngay các công cụ AI cao cấp với giao hàng tự động.", "Hãy chọn một tùy chọn bên dưới để tiếp tục."),
    ("th", "🇹🇭 ไทย (Thailand)", "สินค้า", "สินค้าแนะนำ", "คำสั่งซื้อของฉัน", "ของแจกฟรี", "โปรไฟล์", "ฝากเงิน", "Bot API", "ร้านค้า", "ภาษา", "แนะนำเพื่อนและรับรางวัล", "สมัครเป็นผู้จัดจำหน่าย", "เข้าร่วมช่องของเรา", "ยินดีต้อนรับสู่ RainDeals", "รับเครื่องมือ AI พรีเมียมได้ทันที พร้อมจัดส่งอัตโนมัติ", "เลือกตัวเลือกด้านล่างเพื่อดำเนินการต่อ"),
    ("my", "🇲🇲 မြန်မာ (Myanmar)", "ကုန်ပစ္စည်းများ", "အထူးကုန်ပစ္စည်း", "ကျွန်ုပ်၏အော်ဒါများ", "အခမဲ့လက်ဆောင်များ", "ပရိုဖိုင်", "ငွေဖြည့်ရန်", "Bot API", "စတိုး", "ဘာသာစကား", "မိတ်ဆွေဖိတ်ခေါ်ပြီး ဆုရယူပါ", "ပေးသွင်းသူဖြစ်ရန်", "ကျွန်ုပ်တို့၏ချန်နယ်သို့ဝင်ရန်", "RainDeals မှ ကြိုဆိုပါသည်", "အလိုအလျောက်ပို့ဆောင်မှုဖြင့် ပရီမီယံ AI ကိရိယာများကို ချက်ချင်းရယူပါ။", "ဆက်လက်ရန် အောက်ပါရွေးချယ်မှုတစ်ခုကို ရွေးပါ။"),
    ("km", "🇰🇭 ខ្មែរ (Cambodia)", "ផលិតផល", "ផលិតផលពិសេស", "ការបញ្ជាទិញរបស់ខ្ញុំ", "អំណោយឥតគិតថ្លៃ", "ប្រវត្តិរូប", "ដាក់ប្រាក់", "Bot API", "ហាង", "ភាសា", "អញ្ជើញមិត្ត និងទទួលរង្វាន់", "ក្លាយជាអ្នកផ្គត់ផ្គង់", "ចូលរួមឆានែលរបស់យើង", "សូមស្វាគមន៍មកកាន់ RainDeals", "ទទួលបានឧបករណ៍ AI ពិសេសភ្លាមៗ ជាមួយការដឹកជញ្ជូនស្វ័យប្រវត្តិ។", "សូមជ្រើសរើសជម្រើសខាងក្រោមដើម្បីបន្ត។"),
    ("lo", "🇱🇦 ລາວ (Laos)", "ສິນຄ້າ", "ສິນຄ້າແນະນຳ", "ຄຳສັ່ງຊື້ຂອງຂ້ອຍ", "ຂອງຂວັນຟຣີ", "ໂປຣໄຟລ໌", "ຝາກເງິນ", "Bot API", "ຮ້ານຄ້າ", "ພາສາ", "ແນະນຳໝູ່ແລະຮັບລາງວັນ", "ເປັນຜູ້ສະໜອງ", "ເຂົ້າຮ່ວມຊ່ອງຂອງພວກເຮົາ", "ຍິນດີຕ້ອນຮັບສູ່ RainDeals", "ເຂົ້າເຖິງເຄື່ອງມື AI ພຣີມຽມໄດ້ທັນທີດ້ວຍການຈັດສົ່ງອັດຕະໂນມັດ.", "ເລືອກໜຶ່ງຕົວເລືອກຂ້າງລຸ່ມເພື່ອດຳເນີນຕໍ່."),
    ("ne", "🇳🇵 नेपाली (Nepal)", "उत्पादनहरू", "विशेष उत्पादन", "मेरा अर्डरहरू", "निःशुल्क उपहार", "प्रोफाइल", "जम्मा गर्नुहोस्", "बोट API", "स्टोर", "भाषा", "साथीलाई सिफारिस गरी कमाउनुहोस्", "आपूर्तिकर्ता बन्नुहोस्", "हाम्रो च्यानलमा जोडिनुहोस्", "RainDeals मा स्वागत छ", "पूर्ण स्वचालित डेलिभरीसहित प्रिमियम AI उपकरणहरू तुरुन्त पाउनुहोस्।", "जारी राख्न तलको एउटा विकल्प छान्नुहोस्।"),
    ("si", "🇱🇰 සිංහල (Sri Lanka)", "නිෂ්පාදන", "විශේෂ නිෂ්පාදනය", "මගේ ඇණවුම්", "නොමිලේ තෑගි", "පැතිකඩ", "තැන්පතු", "Bot API", "වෙළඳසැල", "භාෂාව", "යොමු කර ත්‍යාග උපයන්න", "සැපයුම්කරුවෙකු වන්න", "අපගේ නාලිකාවට එක්වන්න", "RainDeals වෙත සාදරයෙන් පිළිගනිමු", "සම්පූර්ණ ස්වයංක්‍රීය බෙදාහැරීම සමඟ වාරික AI මෙවලම් වහාම ලබාගන්න.", "ඉදිරියට යාමට පහත විකල්පයක් තෝරන්න."),
    ("dv", "🇲🇻 ދިވެހި (Maldives)", "ތަކެތި", "ޚާއްސަ ތަކެތި", "އަހަރެންގެ އޯޑަރުތައް", "މިއަކަށް ހިލޭ ހަދިޔާ", "ޕްރޮފައިލް", "ޑިޕޮޒިޓް", "Bot API", "ފިހާރަ", "ބަސް", "ރިފަރ ކޮށް ހޯދާ", "ސަޕްލައިޔަރ ވާށެވެ", "އަހަރެންގެ ޗެނަލަށް ވަދެލާ", "RainDeals އަށް މަރުހަބާ", "އޮޓޮމެޓިކް ޑެލިވަރީއާއެކު ޕްރީމިއަމް AI ޓޫލްތައް ފަހިކަމާއެކު ލިބޭނެ.", "ކުރިއަށް ދިއުމަށް ތިރީގައިވާ އޮޕްޝަނެއް ނަގާ."),
    ("dz", "🇧🇹 རྫོང་ཁ (Bhutan)", "ཐོན་རྫས།", "ཁྱད་འཕགས་ཐོན་རྫས།", "ངའི་མངགས་ཉོ།", "རིན་མེད་སྐྱེས་འབུལ།", "གསལ་སྡུད།", "དངུལ་བཅུག", "Bot API", "ཚོང་ཁང་།", "སྐད་ཡིག", "གྲོགས་པོ་འབོད་ནས་རྔན་པ་ཐོབ།", "མཁོ་སྤྲོད་པ་འགྱུར།", "ང་བཅས་ཀྱི་རྒྱུན་ལམ་ནང་ཞུགས།", "RainDeals ལ་དགའ་བསུ།", "རང་འགུལ་སྤྲོད་ལེན་དང་བཅས་ AI ལག་ཆ་སྤུས་ལེགས་དེ་འཕྲལ་ལ་ཐོབ།", "མུ་མཐུད་ནས་འགྲོ་བར་འོག་གི་གདམ་ག་ཞིག་འདེམས།"),
    ("ru", "🇷🇺 Русский (Russia)", "Товары", "Рекомендуемый товар", "Мои заказы", "Бесплатные подарки", "Профиль", "Пополнить баланс", "Bot API", "Магазин", "Язык", "Приглашай и зарабатывай", "Стать поставщиком", "Наш канал", "Добро пожаловать в RainDeals", "Получайте премиум-инструменты ИИ с полностью автоматической доставкой.", "Выберите один из вариантов ниже, чтобы продолжить."),
    ("uz", "🇺🇿 O‘zbekcha (Uzbek)", "Mahsulotlar", "Tavsiya etilgan mahsulot", "Buyurtmalarim", "Bepul sovg‘alar", "Profil", "Hisobni to‘ldirish", "Bot API", "Do‘kon", "Til", "Do‘st taklif qiling va mukofot oling", "Yetkazib beruvchi bo‘ling", "Kanalimizga qo‘shiling", "RainDeals’ga xush kelibsiz", "Premium AI vositalariga to‘liq avtomatik yetkazib berish orqali darhol ega bo‘ling.", "Davom etish uchun quyidagi variantlardan birini tanlang."),
    ("kk", "🇰🇿 Қазақша (Kazakh)", "Өнімдер", "Ұсынылған өнім", "Тапсырыстарым", "Тегін сыйлықтар", "Профиль", "Баланс толтыру", "Bot API", "Дүкен", "Тіл", "Дос шақырып, сыйақы алыңыз", "Жеткізуші болу", "Арнамызға қосылыңыз", "RainDeals-ке қош келдіңіз", "Премиум AI құралдарын толық автоматты жеткізумен бірден алыңыз.", "Жалғастыру үшін төмендегі нұсқалардың бірін таңдаңыз."),
    ("ky", "🇰🇬 Кыргызча (Kyrgyz)", "Товарлар", "Сунушталган товар", "Буйрутмаларым", "Бекер белектер", "Профиль", "Баланс толтуруу", "Bot API", "Дүкөн", "Тил", "Досуңузду чакырып, сыйлык алыңыз", "Жеткирүүчү болуңуз", "Каналыбызга кошулуңуз", "RainDeals'ке кош келиңиз", "Премиум AI куралдарын толук автоматтык жеткирүү менен дароо алыңыз.", "Улантуу үчүн төмөнкү варианттардын бирин тандаңыз."),
    ("tk", "🇹🇲 Türkmençe (Turkmen)", "Önümler", "Saýlanan önüm", "Sargytlarym", "Mugt sowgatlar", "Profil", "Hasaby doldur", "Bot API", "Dükan", "Dil", "Dostuňy çagyr we sylag gazan", "Üpjün ediji bol", "Kanalymyza goşul", "RainDeals-e hoş geldiňiz", "Premium AI gurallaryny doly awtomatik eltip bermek bilen dessine alyň.", "Dowam etmek üçin aşakdaky görnüşleriň birini saýlaň."),
    ("tg", "🇹🇯 Тоҷикӣ (Tajik)", "Маҳсулот", "Маҳсулоти тавсияшуда", "Фармоишҳои ман", "Тӯҳфаҳои ройгон", "Профил", "Пур кардани ҳисоб", "Bot API", "Мағоза", "Забон", "Дӯстонро даъват кунед ва мукофот гиред", "Таъминкунанда шавед", "Ба канали мо ҳамроҳ шавед", "Ба RainDeals хуш омадед", "Асбобҳои премиуми AI-ро бо расонидани пурра автоматӣ фавран гиред.", "Барои идома яке аз имконоти зерро интихоб кунед."),
    ("az", "🇦🇿 Azərbaycanca (Azerbaijan)", "Məhsullar", "Seçilmiş məhsul", "Sifarişlərim", "Pulsuz hədiyyələr", "Profil", "Balansı artır", "Bot API", "Mağaza", "Dil", "Dost dəvət et, mükafat qazan", "Təchizatçı ol", "Kanalımıza qoşul", "RainDeals-ə xoş gəlmisiniz", "Premium AI alətlərinə tam avtomatik çatdırılma ilə dərhal daxil olun.", "Davam etmək üçün aşağıdan bir seçim edin."),
    ("hy", "🇦🇲 Հայերեն (Armenia)", "Ապրանքներ", "Առաջարկվող ապրանք", "Իմ պատվերները", "Անվճար նվերներ", "Պրոֆիլ", "Հաշվի համալրում", "Bot API", "Խանութ", "Լեզու", "Հրավիրեք ընկերներին և վաստակեք", "Դառնալ մատակարար", "Միացեք մեր ալիքին", "Բարի գալուստ RainDeals", "Ստացեք պրեմիում AI գործիքներ՝ լիովին ավտոմատ առաքմամբ։", "Շարունակելու համար ընտրեք ստորև ներկայացված տարբերակներից մեկը։"),
    ("ka", "🇬🇪 ქართული (Georgia)", "პროდუქტები", "რჩეული პროდუქტი", "ჩემი შეკვეთები", "უფასო საჩუქრები", "პროფილი", "ბალანსის შევსება", "Bot API", "მაღაზია", "ენა", "მოიწვიე მეგობარი და მიიღე ჯილდო", "გახდი მომწოდებელი", "შემოუერთდი ჩვენს არხს", "მოგესალმებით RainDeals-ში", "მიიღეთ პრემიუმ AI ხელსაწყოები სრულად ავტომატური მიწოდებით.", "გასაგრძელებლად აირჩიეთ ერთ-ერთი ვარიანტი ქვემოთ."),
    ("es", "🇪🇸 Español", "Productos", "Producto destacado", "Mis pedidos", "Regalos gratis", "Perfil", "Depositar", "Bot API", "Tienda", "Idioma", "Invita y gana", "Conviértete en proveedor", "Únete a nuestro canal", "Te damos la bienvenida a RainDeals", "Accede al instante a herramientas de IA premium con entrega totalmente automática.", "Elige una opción a continuación para continuar."),
    ("pt_BR", "🇧🇷 Português (Brasil)", "Produtos", "Produto em destaque", "Meus pedidos", "Brindes grátis", "Perfil", "Depositar", "Bot API", "Loja", "Idioma", "Indique e ganhe", "Seja um fornecedor", "Entre no nosso canal", "Boas-vindas ao RainDeals", "Acesse ferramentas de IA premium com entrega totalmente automática.", "Escolha uma opção abaixo para continuar."),
    ("sw", "🇰🇪 Kiswahili (Africa)", "Bidhaa", "Bidhaa maalum", "Oda Zangu", "Zawadi za bure", "Wasifu", "Weka pesa", "Bot API", "Duka", "Lugha", "Alika rafiki upate zawadi", "Kuwa msambazaji", "Jiunge na chaneli yetu", "Karibu RainDeals", "Pata zana za AI za kiwango cha juu mara moja kwa uwasilishaji wa kiotomatiki.", "Chagua mojawapo ya chaguo hapa chini ili uendelee."),
]

_FIELDS = (
    "code", "label", "products", "featured", "orders", "freebies", "profile",
    "deposit", "api", "store", "language", "referrals", "supplier", "channel",
    "welcome", "intro", "prompt",
)
LANGUAGES = {row[0]: dict(zip(_FIELDS, row)) for row in _ROWS}

_GREETINGS = {
    "en": "Hey Dear", "ur": "سلام عزیز", "hi": "नमस्ते प्रिय", "bn": "প্রিয়", "ar": "مرحبًا عزيزي", "fa": "سلام عزیز", "ps": "سلام ګرانه", "tr": "Merhaba sevgili", "zh": "亲爱的", "ja": "こんにちは", "ko": "안녕하세요", "mn": "Сайн байна уу", "id": "Halo", "ms": "Hai", "fil": "Kumusta", "vi": "Xin chào", "th": "สวัสดี", "my": "မင်္ဂလာပါ", "km": "សួស្តី", "lo": "ສະບາຍດີ", "ne": "नमस्ते प्रिय", "si": "ආයුබෝවන්", "dv": "މަރުހަބާ", "dz": "བཀྲ་ཤིས་བདེ་ལེགས", "ru": "Здравствуйте", "uz": "Salom", "kk": "Сәлем", "ky": "Саламатсызбы", "tk": "Salam", "tg": "Салом", "az": "Salam", "hy": "Ողջույն", "ka": "გამარჯობა", "es": "Hola", "pt_BR": "Olá", "sw": "Habari",
}


def normalize_language(code: str | None) -> str:
    code = str(code or "en").strip()
    return code if code in LANGUAGES else "en"


def menu_text(code: str | None, key: str) -> str:
    code = normalize_language(code)
    if key == "hello":
        return _GREETINGS.get(code, _GREETINGS["en"])
    language = LANGUAGES[code]
    return str(language.get(key) or LANGUAGES["en"].get(key) or key)


def language_label(code: str | None) -> str:
    return menu_text(code, "label")
