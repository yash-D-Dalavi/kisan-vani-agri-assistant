"""
seed_data.py - demo knowledge corpus (Hindi / Marathi / English).

HONESTY NOTE (also in README): these are short *demo summaries* written for the IA prototype,
modelled on public ICAR / KVK / scheme-portal guidance.  Every document carries full metadata and an
`approved` flag so the approval workflow can be demonstrated, but before any real deployment each document
must be replaced by (or checked against) the original licensed source and signed off by an agronomist
(blueprint section 1.7 and Module 5).

Special documents that exist on purpose:
  * cotton_pbw_hi  v1  -> an OLD version, auto-superseded by v2 (shows versioning)
  * rumor_spray_hi     -> approved=False WhatsApp-forward style advice (shows that unapproved text is never retrievable)
"""
from __future__ import annotations

from typing import Dict, List

LIC = "Demo summary for educational use - verify against original source before deployment"


def _d(doc_id, topic, title, text, *, lang, crop="all", region="India", season="all", source, publisher,
       date, version=1, approved=True, safety="normal", url="", license=LIC):
    return {
        "doc_id": doc_id, "topic": topic, "title": title, "text": text, "language": lang, "crop": crop,
        "region": region, "season": season, "source": source, "publisher": publisher, "doc_date": date,
        "version": version, "approved": approved, "safety_level": safety, "source_url": url, "license": license,
    }


SEED_DOCUMENTS: List[Dict] = [
    # ------------------------------------------------------------------ cotton: pink bollworm
    _d("cotton_pbw_hi_v1", "cotton_pbw", "कपास गुलाबी सुंडी प्रबंधन (पुराना संस्करण)",
       "कपास में गुलाबी सुंडी की निगरानी के लिए प्रति हेक्टेयर 3 फेरोमोन ट्रैप लगाएं। प्रति ट्रैप 5 पतंगे मिलने पर कार्रवाई करें। "
       "संक्रमित डोडे तोड़कर नष्ट करें।",
       lang="hi", crop="cotton", region="Maharashtra", season="kharif",
       source="ICAR-CICR कपास गुलाबी सुंडी सलाह", publisher="ICAR-CICR (demo summary)", date="2024-03-10", version=1,
       safety="pest"),
    _d("cotton_pbw_hi", "cotton_pbw", "कपास में गुलाबी सुंडी (पिंक बॉलवर्म) प्रबंधन",
       "कपास में गुलाबी सुंडी (पिंक बॉलवर्म) की निगरानी के लिए प्रति हेक्टेयर 5 फेरोमोन ट्रैप लगाएं। "
       "लगातार तीन रातों तक प्रति ट्रैप 8 पतंगे मिलें तो यह आर्थिक हानि स्तर का संकेत है। "
       "संक्रमित फूल और डोडे, जिनमें गुलाब की पंखुड़ी जैसी रोजेट बनावट दिखे, तोड़कर नष्ट करें। "
       "फसल के बाद खेत में कपास के अवशेष और सूखी डोडियां न छोड़ें। "
       "कीटनाशक चुनने और उसकी मात्रा तय करने के लिए कृषि विज्ञान केंद्र (KVK) की सलाह जरूर लें।",
       lang="hi", crop="cotton", region="Maharashtra", season="kharif",
       source="ICAR-CICR कपास गुलाबी सुंडी सलाह", publisher="ICAR-CICR (demo summary)", date="2026-06-15", version=2,
       safety="pest", url="https://cicr.org.in"),
    _d("cotton_pbw_mr", "cotton_pbw", "कापसातील गुलाबी बोंडअळी व्यवस्थापन",
       "कापसातील गुलाबी बोंडअळीच्या निरीक्षणासाठी प्रति हेक्टरी 5 कामगंध सापळे लावा. "
       "सलग तीन रात्री प्रति सापळा 8 पतंग मिळाले तर ते आर्थिक नुकसान पातळीचे लक्षण आहे. "
       "गुलाबाच्या पाकळ्यांसारखे (रोझेट) फूल आणि प्रादुर्भावग्रस्त बोंडे तोडून नष्ट करा. "
       "हंगामानंतर शेतात कापसाचे अवशेष आणि वाळलेली बोंडे ठेवू नका. "
       "कीटकनाशक निवडण्यासाठी आणि त्याचे प्रमाण ठरवण्यासाठी कृषी विज्ञान केंद्राचा (KVK) सल्ला जरूर घ्या.",
       lang="mr", crop="cotton", region="Maharashtra", season="kharif",
       source="ICAR-CICR कापूस गुलाबी बोंडअळी सल्ला", publisher="ICAR-CICR (demo summary)", date="2026-06-15", version=2,
       safety="pest", url="https://cicr.org.in"),
    _d("cotton_pbw_en", "cotton_pbw", "Cotton pink bollworm management",
       "To monitor pink bollworm in cotton, install 5 pheromone traps per hectare. "
       "Catching 8 moths per trap for three consecutive nights indicates the economic threshold level. "
       "Pick and destroy infested flowers and bolls that show a rosette shape. "
       "Do not leave cotton residue or dry bolls in the field after the season. "
       "Consult the Krishi Vigyan Kendra (KVK) before choosing any insecticide and its dose.",
       lang="en", crop="cotton", region="Maharashtra", season="kharif",
       source="ICAR-CICR pink bollworm advisory", publisher="ICAR-CICR (demo summary)", date="2026-06-15", version=2,
       safety="pest", url="https://cicr.org.in"),
    # ------------------------------------------------------------------ cotton: sucking pests
    _d("cotton_sucking_hi", "cotton_sucking", "कपास में रस चूसने वाले कीट",
       "कपास में रस चूसने वाले कीट जैसे माहू, तेला (जैसिड) और सफेद मक्खी पत्तियों के किनारे पीले करके मोड़ देते हैं। "
       "पौधों की नियमित निगरानी करें और प्रति एकड़ 8 से 10 पीले चिपचिपे जाल लगाएं। "
       "शुरुआत में नीम बीज अर्क 5 प्रतिशत का छिड़काव करें। "
       "बिना जरूरत रासायनिक कीटनाशक न छिड़कें और सलाह के लिए KVK से संपर्क करें।",
       lang="hi", crop="cotton", region="Maharashtra", season="kharif",
       source="महाराष्ट्र कृषि विश्वविद्यालय कपास कीट सलाह", publisher="State Agricultural University (demo summary)",
       date="2026-05-20", safety="pest"),
    _d("cotton_sucking_mr", "cotton_sucking", "कापसावरील रस शोषक किडी",
       "कापसावर मावा, तुडतुडे आणि पांढरी माशी यांसारख्या रस शोषक किडी पानांच्या कडा पिवळ्या करून पाने वळवतात. "
       "पिकाचे नियमित निरीक्षण करा आणि प्रति एकर 8 ते 10 पिवळे चिकट सापळे लावा. "
       "सुरुवातीला निंबोळी अर्क 5 टक्के फवारा. "
       "गरज नसताना रासायनिक कीटकनाशके फवारू नका आणि सल्ल्यासाठी KVK शी संपर्क साधा.",
       lang="mr", crop="cotton", region="Maharashtra", season="kharif",
       source="महाराष्ट्र कृषी विद्यापीठ कापूस कीड सल्ला", publisher="State Agricultural University (demo summary)",
       date="2026-05-20", safety="pest"),
    # ------------------------------------------------------------------ cotton: yellowing (blueprint example)
    _d("cotton_yellow_hi", "cotton_yellow", "कपास की पत्तियां पीली पड़ना",
       "कपास की पत्तियां पीली पड़ने के कई कारण हो सकते हैं। रस चूसने वाले कीट पत्तियों के किनारे पीले कर देते हैं, "
       "इसलिए पत्तियों के नीचे कीट देखें। नाइट्रोजन की कमी से पुरानी पत्तियां एक समान पीली होती हैं और "
       "मैग्नीशियम की कमी से नसों के बीच का हिस्सा पीला या लाल होता है। भारी बारिश के बाद जलभराव हो तो पानी की निकासी करें। "
       "सही कारण जानने के लिए मिट्टी की जांच कराएं और खाद या दवा देने से पहले KVK से सलाह लें।",
       lang="hi", crop="cotton", region="Maharashtra", season="kharif",
       source="KVK कपास पोषण एवं कीट सलाह", publisher="Krishi Vigyan Kendra (demo summary)", date="2026-07-02", safety="pest"),
    _d("cotton_yellow_mr", "cotton_yellow", "कापसाची पाने पिवळी पडणे",
       "कापसाची पाने पिवळी पडण्याची अनेक कारणे असू शकतात. रस शोषक किडी पानांच्या कडा पिवळ्या करतात, "
       "म्हणून पानांच्या खालच्या बाजूला कीड पाहा. नत्राच्या कमतरतेमुळे जुनी पाने एकसारखी पिवळी होतात आणि "
       "मॅग्नेशियमच्या कमतरतेमुळे शिरांमधील भाग पिवळा किंवा लाल होतो. जोरदार पावसानंतर शेतात पाणी साचले असेल तर निचरा करा. "
       "नेमके कारण समजण्यासाठी माती परीक्षण करा आणि खत किंवा औषध देण्यापूर्वी KVK चा सल्ला घ्या.",
       lang="mr", crop="cotton", region="Maharashtra", season="kharif",
       source="KVK कापूस पोषण व कीड सल्ला", publisher="Krishi Vigyan Kendra (demo summary)", date="2026-07-02", safety="pest"),
    # ------------------------------------------------------------------ soybean yellow
    _d("soybean_yellow_hi", "soybean_yellow", "सोयाबीन में पत्तियों का पीलापन",
       "सोयाबीन की पत्तियों पर पीले-हरे चितकबरे धब्बे दिखें तो यह पीला मोज़ेक रोग हो सकता है, जिसे सफेद मक्खी फैलाती है। "
       "ऐसे रोगी पौधों को उखाड़कर नष्ट करें और पीले चिपचिपे जाल लगाएं। "
       "यदि भारी बारिश के बाद पूरे खेत में एक समान पीलापन हो तो जल निकास ठीक करें और मिट्टी की जांच कराएं। "
       "रोग की पुष्टि और दवा की सलाह के लिए KVK से संपर्क करें।",
       lang="hi", crop="soybean", region="Maharashtra", season="kharif",
       source="ICAR-IISR सोयाबीन रोग सलाह", publisher="ICAR-IISR (demo summary)", date="2026-06-28", safety="pest"),
    _d("soybean_yellow_mr", "soybean_yellow", "सोयाबीनची पाने पिवळी पडणे",
       "सोयाबीनच्या पानांवर पिवळे डाग दिसत असतील तर तो पिवळा मोझॅक रोग असू शकतो, जो पांढरी माशी पसरवते. "
       "असे रोगट झाड उपटून नष्ट करा आणि पिवळे चिकट सापळे लावा. "
       "जोरदार पावसानंतर संपूर्ण शेतात एकसारखा पिवळेपणा दिसला तर पाण्याचा निचरा करा आणि माती परीक्षण करा. "
       "रोगाच्या खात्रीसाठी आणि औषधाच्या सल्ल्यासाठी KVK शी संपर्क साधा.",
       lang="mr", crop="soybean", region="Maharashtra", season="kharif",
       source="ICAR-IISR सोयाबीन रोग सल्ला", publisher="ICAR-IISR (demo summary)", date="2026-06-28", safety="pest"),
    # ------------------------------------------------------------------ tomato late blight (DOSE-bearing)
    _d("tomato_blight_hi", "tomato_blight", "टमाटर में पछेती झुलसा (लेट ब्लाइट)",
       "टमाटर में पछेती झुलसा (लेट ब्लाइट) में पत्तियों पर गहरे भूरे जलीय धब्बे बनते हैं और नमी में नीचे सफेद फफूंद दिखती है। "
       "रोगी पौधे उखाड़कर नष्ट करें और ऊपर से सिंचाई न करें। "
       "सुरक्षात्मक छिड़काव के लिए मैंकोज़ेब 75 प्रतिशत WP की मात्रा 2.5 ग्राम प्रति लीटर पानी है, जिसे 7 से 10 दिन के अंतर पर दोहराया जा सकता है। "
       "लेबल पर लिखी मात्रा और तुड़ाई से पहले की प्रतीक्षा अवधि का पालन करें, और दस्ताने तथा मास्क पहनें।",
       lang="hi", crop="tomato", region="India", season="all",
       source="ICAR-IIHR टमाटर रोग प्रबंधन", publisher="ICAR-IIHR (demo summary)", date="2026-04-18", safety="dose"),
    _d("tomato_blight_mr", "tomato_blight", "टोमॅटोवरील लेट ब्लाइट (करपा)",
       "टोमॅटोवरील लेट ब्लाइट (करपा) रोगात पानांवर गडद तपकिरी पाणथळ डाग पडतात आणि दमट हवामानात खाली पांढरी बुरशी दिसते. "
       "रोगट झाडे उपटून नष्ट करा आणि वरून पाणी देणे टाळा. "
       "संरक्षक फवारणीसाठी मॅन्कोझेब 75 टक्के WP चे प्रमाण 2.5 ग्रॅम प्रति लिटर पाणी आहे, ते 7 ते 10 दिवसांच्या अंतराने पुन्हा फवारता येते. "
       "लेबलवर दिलेले प्रमाण आणि काढणीपूर्वीचा प्रतीक्षा कालावधी पाळा, तसेच हातमोजे आणि मास्क वापरा.",
       lang="mr", crop="tomato", region="India", season="all",
       source="ICAR-IIHR टोमॅटो रोग व्यवस्थापन", publisher="ICAR-IIHR (demo summary)", date="2026-04-18", safety="dose"),
    # ------------------------------------------------------------------ wheat sowing
    _d("wheat_sowing_hi", "wheat_sowing", "गेहूं की बुवाई (महाराष्ट्र)",
       "महाराष्ट्र में सिंचित क्षेत्र में गेहूं की बुवाई नवंबर के पहले पखवाड़े में, यानी 1 से 15 नवंबर के बीच करें। "
       "बीज दर लगभग 100 किलो प्रति हेक्टेयर रखें और कतारों के बीच 20 से 22.5 सेंटीमीटर दूरी रखें। "
       "बुवाई से पहले बीज उपचार करें। "
       "देर से बुवाई करने पर उपज घट सकती है, इसलिए स्थानीय कृषि विश्वविद्यालय या KVK की सलाह लें।",
       lang="hi", crop="wheat", region="Maharashtra", season="rabi",
       source="MPKV गेहूं उत्पादन तकनीक", publisher="State Agricultural University (demo summary)", date="2026-09-10"),
    _d("wheat_sowing_mr", "wheat_sowing", "गहू पेरणी (महाराष्ट्र)",
       "महाराष्ट्रात बागायती क्षेत्रात गव्हाची पेरणी नोव्हेंबरच्या पहिल्या पंधरवड्यात, म्हणजे 1 ते 15 नोव्हेंबर दरम्यान करा. "
       "बियाणे दर सुमारे 100 किलो प्रति हेक्टर ठेवा आणि ओळींमध्ये 20 ते 22.5 सेंटीमीटर अंतर ठेवा. "
       "पेरणीपूर्वी बीजप्रक्रिया करा. "
       "उशिरा पेरणी केल्यास उत्पादन घटू शकते, म्हणून स्थानिक कृषी विद्यापीठ किंवा KVK चा सल्ला घ्या.",
       lang="mr", crop="wheat", region="Maharashtra", season="rabi",
       source="MPKV गहू उत्पादन तंत्रज्ञान", publisher="State Agricultural University (demo summary)", date="2026-09-10"),
    # ------------------------------------------------------------------ onion storage
    _d("onion_storage_hi", "onion_storage", "प्याज की कटाई के बाद क्योरिंग और भंडारण",
       "प्याज की कटाई के बाद उसे खेत में 2 से 3 दिन सुखाएं और फिर छाया में 7 से 10 दिन क्योरिंग करें, जब तक गर्दन सूखकर कस न जाए। "
       "कटाई में गर्दन लगभग 2.5 सेंटीमीटर छोड़ें। "
       "भंडारण हवादार ढांचे में करें और प्याज को भीगने न दें। "
       "सड़े या कटे हुए कंद अलग कर दें।",
       lang="hi", crop="onion", region="Maharashtra", season="rabi",
       source="ICAR-DOGR प्याज भंडारण सलाह", publisher="ICAR-DOGR (demo summary)", date="2026-03-12"),
    _d("onion_storage_mr", "onion_storage", "कांदा काढणीनंतर क्युरिंग आणि साठवण",
       "कांद्याच्या काढणीनंतर तो शेतात 2 ते 3 दिवस वाळवा आणि नंतर सावलीत 7 ते 10 दिवस क्युरिंग करा, मान वाळून घट्ट होईपर्यंत. "
       "काढणी करताना सुमारे 2.5 सेंटीमीटर मान ठेवा. "
       "साठवण हवेशीर ढाच्यात करा आणि कांदा भिजू देऊ नका. "
       "सडलेले किंवा कापलेले कांदे वेगळे करा.",
       lang="mr", crop="onion", region="Maharashtra", season="rabi",
       source="ICAR-DOGR कांदा साठवण सल्ला", publisher="ICAR-DOGR (demo summary)", date="2026-03-12"),
    # ------------------------------------------------------------------ PM-KISAN
    _d("pmkisan_hi", "pmkisan", "पीएम किसान सम्मान निधि योजना",
       "पीएम किसान सम्मान निधि योजना में पात्र किसान परिवारों को हर साल 6000 रुपये मिलते हैं, जो 2000 रुपये की तीन समान किस्तों में हर चार महीने पर सीधे बैंक खाते में आते हैं। "
       "लाभ के लिए ई-केवाईसी, आधार से जुड़ा बैंक खाता और भूमि अभिलेख का सत्यापन जरूरी है। "
       "स्थिति और लाभार्थी सूची pmkisan.gov.in पर देखें। "
       "राशि और नियम बदल सकते हैं, इसलिए पोर्टल या कृषि कार्यालय से पुष्टि करें।",
       lang="hi", region="India", source="PM-KISAN पोर्टल", publisher="Ministry of Agriculture & Farmers Welfare (demo summary)",
       date="2026-02-01", url="https://pmkisan.gov.in"),
    _d("pmkisan_mr", "pmkisan", "पीएम किसान सन्मान निधी योजना",
       "पीएम किसान सन्मान निधी योजनेत पात्र शेतकरी कुटुंबांना दरवर्षी 6000 रुपये मिळतात, जे 2000 रुपयांच्या तीन समान हप्त्यांमध्ये दर चार महिन्यांनी थेट बँक खात्यात जमा होतात. "
       "लाभासाठी ई-केवायसी, आधारशी जोडलेले बँक खाते आणि जमिनीच्या अभिलेखाची पडताळणी आवश्यक आहे. "
       "स्थिती आणि लाभार्थी यादी pmkisan.gov.in वर पाहा. "
       "रक्कम आणि नियम बदलू शकतात, म्हणून पोर्टल किंवा कृषी कार्यालयाकडून खात्री करा.",
       lang="mr", region="India", source="पीएम किसान पोर्टल", publisher="Ministry of Agriculture & Farmers Welfare (demo summary)",
       date="2026-02-01", url="https://pmkisan.gov.in"),
    _d("pmkisan_en", "pmkisan", "PM-KISAN Samman Nidhi scheme",
       "Under the PM-KISAN Samman Nidhi scheme, eligible farmer families receive 6000 rupees every year in three equal instalments of 2000 rupees, "
       "paid every four months directly into the bank account. "
       "e-KYC, an Aadhaar-linked bank account and land record verification are required. "
       "Check status and the beneficiary list on pmkisan.gov.in. "
       "Amounts and rules can change, so confirm on the portal or at the agriculture office.",
       lang="en", region="India", source="PM-KISAN portal", publisher="Ministry of Agriculture & Farmers Welfare (demo summary)",
       date="2026-02-01", url="https://pmkisan.gov.in"),
    # ------------------------------------------------------------------ PMFBY crop insurance
    _d("pmfby_hi", "pmfby", "प्रधानमंत्री फसल बीमा योजना",
       "प्रधानमंत्री फसल बीमा योजना में किसान को खरीफ की खाद्यान्न और तिलहन फसलों के लिए बीमित राशि का 2 प्रतिशत, रबी की इन फसलों के लिए 1.5 प्रतिशत "
       "और वार्षिक वाणिज्यिक या बागवानी फसलों के लिए 5 प्रतिशत प्रीमियम देना होता है। "
       "नामांकन अंतिम तिथि से पहले बैंक, CSC या pmfby.gov.in पर करें। "
       "स्थानीय आपदा से नुकसान होने पर 72 घंटे के भीतर क्रॉप इंश्योरेंस ऐप या हेल्पलाइन 14447 पर सूचना दें। "
       "राज्य की योजनाओं में नियम अलग हो सकते हैं, इसलिए कृषि कार्यालय से पुष्टि करें।",
       lang="hi", region="India", season="all", source="PMFBY पोर्टल", publisher="Ministry of Agriculture & Farmers Welfare (demo summary)",
       date="2026-03-05", url="https://pmfby.gov.in"),
    _d("pmfby_mr", "pmfby", "प्रधानमंत्री पीक विमा योजना",
       "प्रधानमंत्री पीक विमा योजनेत शेतकऱ्याला खरीप हंगामातील अन्नधान्य आणि गळीत धान्य पिकांसाठी विमा संरक्षित रकमेच्या 2 टक्के, रब्बी हंगामातील या पिकांसाठी 1.5 टक्के "
       "आणि वार्षिक व्यावसायिक किंवा फळबाग पिकांसाठी 5 टक्के प्रीमियम भरावा लागतो. "
       "नोंदणी अंतिम तारखेपूर्वी बँक, CSC किंवा pmfby.gov.in वर करा. "
       "स्थानिक आपत्तीमुळे नुकसान झाल्यास 72 तासांच्या आत क्रॉप इन्शुरन्स अॅप किंवा हेल्पलाइन 14447 वर कळवा. "
       "राज्य योजनांचे नियम वेगळे असू शकतात, म्हणून कृषी कार्यालयाकडून खात्री करा.",
       lang="mr", region="India", season="all", source="PMFBY पोर्टल", publisher="Ministry of Agriculture & Farmers Welfare (demo summary)",
       date="2026-03-05", url="https://pmfby.gov.in"),
    # ------------------------------------------------------------------ Kisan Call Centre
    _d("kcc_hi", "kcc", "किसान कॉल सेंटर हेल्पलाइन",
       "किसान कॉल सेंटर का टोल फ्री नंबर 1800-180-1551 है। यह सेवा सुबह 6 बजे से रात 10 बजे तक उपलब्ध है और किसान अपनी भाषा में कृषि विशेषज्ञ से बात कर सकते हैं। "
       "फसल, कीट और योजना से जुड़े सवालों के लिए आप अपने नजदीकी कृषि विज्ञान केंद्र (KVK) से भी संपर्क कर सकते हैं।",
       lang="hi", region="India", source="किसान कॉल सेंटर", publisher="Ministry of Agriculture & Farmers Welfare (demo summary)", date="2026-01-15"),
    _d("kcc_mr", "kcc", "किसान कॉल सेंटर हेल्पलाइन",
       "किसान कॉल सेंटरचा टोल फ्री क्रमांक 1800-180-1551 आहे. ही सेवा सकाळी 6 ते रात्री 10 पर्यंत उपलब्ध असते आणि शेतकरी आपल्या भाषेत कृषी तज्ज्ञांशी बोलू शकतात. "
       "पीक, कीड आणि योजनांशी संबंधित प्रश्नांसाठी तुम्ही जवळच्या कृषी विज्ञान केंद्राशी (KVK) देखील संपर्क साधू शकता.",
       lang="mr", region="India", source="किसान कॉल सेंटर", publisher="Ministry of Agriculture & Farmers Welfare (demo summary)", date="2026-01-15"),
    _d("kcc_en", "kcc", "Kisan Call Centre helpline",
       "The Kisan Call Centre toll-free number is 1800-180-1551. The service runs from 6 am to 10 pm and farmers can speak to an agriculture expert in their own language. "
       "For questions about crops, pests and schemes you can also contact your nearest Krishi Vigyan Kendra (KVK).",
       lang="en", region="India", source="Kisan Call Centre", publisher="Ministry of Agriculture & Farmers Welfare (demo summary)", date="2026-01-15"),
    # ------------------------------------------------------------------ soil health / fertilizer
    _d("soil_fert_hi", "soil_fert", "मृदा परीक्षण और संतुलित खाद",
       "मृदा स्वास्थ्य कार्ड योजना के तहत हर 2 साल में मिट्टी की जांच कराएं और जांच रिपोर्ट के अनुसार खाद दें। "
       "नाइट्रोजन खाद एक बार में न डालकर 2 से 3 भागों में दें और यूरिया की अधिक मात्रा से बचें। "
       "जैविक खाद और गोबर की खाद मिलाने से मिट्टी की सेहत सुधरती है। "
       "मिट्टी जांच के लिए नजदीकी कृषि कार्यालय या KVK से संपर्क करें।",
       lang="hi", region="India", season="all", source="मृदा स्वास्थ्य कार्ड पोर्टल", publisher="Dept. of Agriculture & Farmers Welfare (demo summary)",
       date="2026-02-20", url="https://soilhealth.dac.gov.in"),
    _d("soil_fert_mr", "soil_fert", "माती परीक्षण आणि संतुलित खते",
       "मृदा आरोग्य पत्रिका योजनेअंतर्गत दर 2 वर्षांनी माती परीक्षण करा आणि अहवालानुसार खते द्या. "
       "नत्र खत एकदम न देता 2 ते 3 भागांमध्ये विभागून द्या आणि युरियाचा अतिवापर टाळा. "
       "सेंद्रिय खत आणि शेणखत मिसळल्याने जमिनीचे आरोग्य सुधारते. "
       "माती परीक्षणासाठी जवळच्या कृषी कार्यालयाशी किंवा KVK शी संपर्क साधा.",
       lang="mr", region="India", season="all", source="मृदा आरोग्य पत्रिका पोर्टल", publisher="Dept. of Agriculture & Farmers Welfare (demo summary)",
       date="2026-02-20", url="https://soilhealth.dac.gov.in"),
    # ------------------------------------------------------------------ UNAPPROVED rumor (must never be retrievable)
    _d("rumor_spray_hi", "rumor_spray", "व्हाट्सऐप फॉरवर्ड: हर रोग का चमत्कारी इलाज",
       "कपास पर किसी भी दवा की दोगुनी मात्रा मिलाकर छिड़कें, इससे हर रोग और हर कीट खत्म हो जाता है। "
       "कीटनाशक की दुगनी मात्रा से कपास की पैदावार पक्की बढ़ती है।",
       lang="hi", crop="cotton", region="India", season="all", source="WhatsApp forward (unverified)", publisher="Unknown",
       date="2026-08-01", approved=False, safety="dose"),
]

# Extra glossary entries the admin console can display / extend (term -> translations)
GLOSSARY_TERMS: Dict[str, Dict[str, str]] = {
    "cotton": {"hi": "कपास", "mr": "कापूस", "en": "cotton"},
    "pink bollworm": {"hi": "गुलाबी सुंडी", "mr": "गुलाबी बोंडअळी", "en": "pink bollworm"},
    "soybean": {"hi": "सोयाबीन", "mr": "सोयाबीन", "en": "soybean"},
    "late blight": {"hi": "पछेती झुलसा", "mr": "करपा (लेट ब्लाइट)", "en": "late blight"},
    "mancozeb": {"hi": "मैंकोज़ेब", "mr": "मॅन्कोझेब", "en": "mancozeb"},
    "pheromone trap": {"hi": "फेरोमोन ट्रैप", "mr": "कामगंध सापळा", "en": "pheromone trap"},
    "PM-KISAN": {"hi": "पीएम किसान", "mr": "पीएम किसान", "en": "PM-KISAN"},
    "crop insurance": {"hi": "फसल बीमा", "mr": "पीक विमा", "en": "crop insurance"},
    "Kisan Call Centre": {"hi": "किसान कॉल सेंटर", "mr": "किसान कॉल सेंटर", "en": "Kisan Call Centre"},
    "KVK": {"hi": "कृषि विज्ञान केंद्र", "mr": "कृषी विज्ञान केंद्र", "en": "Krishi Vigyan Kendra"},
    "kharif": {"hi": "खरीफ", "mr": "खरीप", "en": "kharif"},
    "rabi": {"hi": "रबी", "mr": "रब्बी", "en": "rabi"},
}
