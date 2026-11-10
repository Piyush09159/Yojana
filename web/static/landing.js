'use strict';
document.documentElement.classList.add('js');

// Public copy is local, including translations. No model or analytics request
// is needed to read the website or change its language.
const translations = {
  hi: {
    skip: 'मुख्य सामग्री पर जाएँ', language: 'वेबसाइट की भाषा',
    navHow: 'कैसे काम करता है', navFeatures: 'सुविधाएँ', navFaq: 'सामान्य प्रश्न', openApp: 'ऐप खोलें',
    eyebrow: 'लोगों के लिए बना। जानकारी पर आधारित।',
    heroLine1: 'सही सहायता।', heroLine2: 'आगे का रास्ता अब साफ़।',
    heroDescription: 'सरकारी योजनाओं को समझना मुश्किल हो सकता है। अपनी स्थिति अपने शब्दों में बताएँ। संभावित लाभ जानें, ज़रूरी शर्तें समझें और अपना अगला कदम तय करें।',
    getStarted: 'शुरू करें', exploreFeatures: 'सुविधाएँ देखें', heroNote: 'शुरू करने के लिए प्रोफ़ाइल फ़ॉर्म नहीं। बस बातचीत।',
    yourLanguage: 'आपके शब्द। आपकी भाषा। आपकी गति।', languages: 'English · हिन्दी · मराठी',
    artTitle: 'थोड़ी स्पष्टता, आगे बढ़ने में मदद।', artStep1: '०१ / शुरुआत आपसे', artStory: 'अपनी बात बताएँ', artStoryNote: 'एक बातचीत, लंबी प्रश्नावली नहीं।',
    artStep2: '०२ / ज़रूरी जानकारी जोड़ें', artReason: 'समझें कि क्या मायने रखता है', artStep3: '०३ / आगे बढ़ें', artNext: 'अगला कदम, साफ़ शब्दों में।', artNextNote: 'शर्तें। स्रोत। स्पष्ट मार्गदर्शन।', artCaption: 'यह प्रक्रिया का चित्रण है, वास्तविक पात्रता परिणाम नहीं।',
    principle1: 'बातचीत से शुरुआत', principle2: 'तर्क समझाने वाले नियम', principle3: 'आधिकारिक स्रोत और स्पष्ट सीमाएँ',
    howEyebrow: 'कम अनुमान। ज़्यादा स्पष्टता।', howTitle: '“कहाँ से शुरू करूँ?” से\n“अब यह करना है” तक।',
    howIntro: 'आपको योजनाओं के नाम या सरकारी शब्दावली जानने की ज़रूरत नहीं। अपनी ज़िंदगी के बारे में जो जानते हैं, वहीं से शुरू करें।',
    step1Title: 'अपनी स्थिति बताएँ', step1Text: 'पढ़ाई के लिए सहायता चाहिए? खेती से जुड़ी योजनाएँ जाननी हैं? अपनी ज़रूरत अंग्रेज़ी, हिन्दी या मराठी में बताएँ।',
    step2Title: 'छूटी हुई जानकारी जोड़ें', step2Text: 'साथी ज़रूरी सवाल पूछता है और उनका कारण बताता है। AI से अलग, कोड में बने नियम पात्रता की जाँच करते हैं।',
    step3Title: 'अगला कदम समझें', step3Text: 'संभावित योजनाएँ, दस्तावेज़ और आधिकारिक स्रोत देखें। आवेदन अटका हो तो रेस्क्यू मोड से बाधाएँ समझें।',
    featuresEyebrow: 'सवाल से अगले कदम तक आपका साथी', featuresTitle: 'मुश्किल बातों को\nसमझने में थोड़ी मदद।', tryWorkspace: 'वर्कस्पेस आज़माएँ',
    discoverTitle: 'सहज बातचीत। नई संभावनाएँ।', discoverText: 'AI आपकी रोज़मर्रा की भाषा से उपयोगी प्रोफ़ाइल जानकारी समझने में मदद करता है। लंबा फ़ॉर्म भरे बिना मौजूदा कैटलॉग की योजनाओं के सुझाव देखें।',
    illustration: 'उदाहरण के लिए बातचीत', previewUser: '“मैं महाराष्ट्र में किसान हूँ। कहाँ से शुरू करूँ?”', previewAssistant: 'कैटलॉग की योजनाओं के लिए ज़रूरी बातें जाँचते हैं। क्या आपके पास खेती योग्य ज़मीन है?', previewReason: 'क्यों ज़रूरी है: कुछ योजनाओं में ज़मीन के स्वामित्व की शर्त होती है।',
    reasonTitle: 'हर सवाल का एक कारण।', reasonText: 'जानकारी अधूरी हो तो पूछा जाता है, अनुमान नहीं लगाया जाता। पात्रता के नियम ज़रूरी सवाल चुनते हैं। संभावित मेल, छूटी जानकारी और मैन्युअल जाँच अलग दिखाई जाती हैं।',
    reasonBranch1: 'आपकी जानकारी', reasonBranch2: 'योजना के नियम', reasonBranch3: 'क्या जानकारी बाकी है?', exploreSchemes: 'योजनाओं का मार्गदर्शन देखें',
    rescueLabel: 'रेस्क्यू मोड', rescueTitle: 'आवेदन अटका है।\nआपको नहीं अटकना चाहिए।', rescueText: 'समर्थित योजनाओं के लिए दस्तावेज़ों में नाम की तुलना करें, आपके बताए पोर्टल संदेश समझें और अधूरी शर्तें पहचानें। सुधार के सुझाव और सही जगह की जानकारी पाएँ।',
    exploreRescue: 'रेस्क्यू मोड देखें', rescueVisualLabel: 'बाधाओं की एक स्पष्ट तस्वीर', rescueFlow1: 'दी गई दस्तावेज़ जानकारी की तुलना', rescueFlow2: 'अधूरी ज़रूरी शर्तों की पहचान', rescueFlow3: 'सुधार का अगला कदम', rescueDisclaimer: 'आपकी दी गई जानकारी पर आधारित। सरकारी रिकॉर्ड या भुगतान की वास्तविक स्थिति की पुष्टि नहीं होती।',
    channelsEyebrow: 'मदद पाने के और रास्ते', channelsTitle: 'आप जहाँ हैं,\nमदद वहाँ तक।', channelsIntro: 'वेब वर्कस्पेस अभी उपलब्ध है। बाकी माध्यमों के लिए वास्तविक सर्वर सेटअप चाहिए, केवल एक बटन नहीं।',
    whatsappBadge: 'सहमति आधारित बीटा', whatsappTitle: 'परिचित बातचीत। WhatsApp पर।', whatsappText: 'मौजूदा WAHA ब्रिज व्यवस्थापक द्वारा कॉन्फ़िगर और पेयर किए जाने पर सहमति आधारित टेक्स्ट बातचीत कर सकता है। उस सेटअप पर YOJANA भेजकर शुरू और STOP भेजकर बंद करें।',
    whatsappInitial: 'उपलब्धता के लिए सर्वर कॉन्फ़िगरेशन ज़रूरी है।', channelDetails: 'माध्यमों की जानकारी देखें', comingSoon: 'जल्द आ रहा है',
    voiceTitle: 'कभी-कभी बोलना आसान होता है।', voiceText: 'LiveKit इंटीग्रेशन के साथ आवाज़ से सहायता की योजना है। ब्राउज़र कॉल और वॉइस नोट अभी उपलब्ध नहीं हैं।', voiceNote: 'अभी योजना में है, जुड़ा नहीं है। माइक्रोफ़ोन की अनुमति नहीं माँगी जाती।',
    privacyEyebrow: 'आपकी जानकारी। आपकी पसंद।', privacyTitle: 'उपयोगी मार्गदर्शन की शुरुआत भरोसे से।', privacyText: 'केवल वही जानकारी दें जो आप देना चाहें। आधार नंबर, बैंक विवरण, फ़ोन नंबर या दस्तावेज़ों की तस्वीरें न भेजें। भेजे गए संदेश सर्वर और कॉन्फ़िगर की गई भाषा सेवा द्वारा संसाधित होते हैं; सेवा प्रदाता की लॉगिंग नीतियाँ लागू हो सकती हैं।',
    privacyPoint1: 'वेब वर्कस्पेस आज़माने के लिए खाता बनाना ज़रूरी नहीं।', privacyPoint2: 'बातचीत के सत्र अस्थायी हैं और सर्वर की मेमोरी में रहते हैं। सेवा लॉग रखे जा सकते हैं।', privacyPoint3: 'आपकी ओर से किसी सरकारी पोर्टल पर कुछ जमा नहीं किया जाता।', startYourTerms: 'अपनी पसंद से शुरू करें',
    faqEyebrow: 'कुछ बातें जानना ज़रूरी है', faqTitle: 'सही सवाल।\nस्पष्ट जवाब।', faqIntro: 'Yojana-Pilot एक स्वतंत्र सहायता परियोजना है। जानें कि यह किन बातों में मदद कर सकती है और इसकी सीमाएँ क्या हैं।',
    faq1Q: 'क्या Yojana-Pilot आधिकारिक सरकारी सेवा है?', faq1A: 'नहीं। Yojana-Pilot एक स्वतंत्र परियोजना है, सरकारी पोर्टल या स्वीकृत सरकारी भागीदार नहीं। यह जानकारी के लिए मार्गदर्शन और आधिकारिक स्रोतों के लिंक देता है।',
    faq2Q: 'क्या सुझाव मिलने का मतलब है कि मैं पात्र हूँ?', faq2A: 'नहीं। सुझाव आपकी जानकारी और कैटलॉग में उपलब्ध नियमों पर आधारित हैं। अधूरी जानकारी, बिना समीक्षा के विवरण या अतिरिक्त शर्तें परिणाम बदल सकती हैं। मौजूदा नियम आधिकारिक सरकारी वेबसाइट पर जाँचें।',
    faq3Q: 'कौन-सी योजनाएँ और भाषाएँ समर्थित हैं?', faq3A: 'आप अंग्रेज़ी, हिन्दी या मराठी में लिख सकते हैं। उपलब्धता मौजूदा कैटलॉग पर निर्भर है, जिसमें केंद्र और महाराष्ट्र की योजनाएँ शामिल हैं; यह भारत की हर योजना की पूरी सूची नहीं है। भाषा समझने के लिए कॉन्फ़िगर की गई मॉडल सेवा चाहिए।',
    faq4Q: 'क्या रेस्क्यू मोड भुगतान जाँच या अपील जमा कर सकता है?', faq4A: 'वास्तविक भुगतान या लाभार्थी रिकॉर्ड नहीं देखे जाते और अपील जमा नहीं होती। कॉन्फ़िगर की गई योजनाओं के लिए आपके बताए पोर्टल संदेश का अर्थ समझाकर जाँच और सुधार के सुझाव दिए जाते हैं।',
    faq5Q: 'क्या पहचान के दस्तावेज़ अपलोड करने होंगे?', faq5A: 'नहीं। दस्तावेज़ अपलोड न करें और पहचान या खाता नंबर न डालें। रेस्क्यू में नामों की वैकल्पिक तुलना केवल आपके टाइप किए नामों से होती है। केवल वही जानकारी दें जिसे इस सेवा को भेजना आपको स्वीकार हो।',
    faq6Q: 'क्या WhatsApp या कॉल से मदद मिल सकती है?', faq6A: 'WhatsApp टेक्स्ट सहायता के लिए व्यवस्थापक द्वारा कॉन्फ़िगर किया WAHA और आपकी सहमति ज़रूरी है। कॉन्फ़िगरेशन से कनेक्शन की गारंटी नहीं मिलती। LiveKit कॉल और वॉइस नोट अभी योजना में हैं, काम नहीं करते।',
    closingEyebrow: 'एक बातचीत से शुरुआत हो सकती है।', closingTitle: 'आइए, आपका अगला कदम ढूँढ़ें।', closingText: 'अपने सवाल लाएँ। हम संभावनाओं को समझने में मदद करेंगे।',
    footerAbout: 'एक स्वतंत्र नागरिक-तकनीक परियोजना, जो बातचीत के ज़रिए योजनाओं की जानकारी समझना आसान बनाती है।', footerExplore: 'देखें', footerWorkspace: 'नागरिक वर्कस्पेस', footerPrivacy: 'गोपनीयता और विकल्प', footerProject: 'परियोजना के बारे में', footerSource: 'GitHub पर स्रोत ↗', footerContact: 'संपर्क / समस्या बताएँ ↗', footerLegacy: 'पुराना फ़ॉर्म इंटरफ़ेस', footerDisclaimer: 'स्वतंत्र मार्गदर्शन। किसी सरकारी एजेंसी से संबद्ध नहीं।', footerDocs: 'API दस्तावेज़ ↗',
    wahaUnconfigured: 'इस सर्वर पर कॉन्फ़िगर नहीं है।', wahaDisabled: 'कॉन्फ़िगर है, लेकिन बंद है। कनेक्शन की पुष्टि नहीं हुई।', wahaEnabled: 'कॉन्फ़िगर है और चालू है। कनेक्शन की पुष्टि नहीं हुई।', wahaUnavailable: 'स्थिति उपलब्ध नहीं। ऐप में सेटअप की जानकारी देखें।',
    menuOpen: 'नेविगेशन खोलें', menuClose: 'नेविगेशन बंद करें', pageTitle: 'Yojana-Pilot — सहायता तक पहुँचने का स्पष्ट रास्ता'
  },
  mr: {
    skip: 'मुख्य मजकुराकडे जा', language: 'वेबसाइटची भाषा',
    navHow: 'कसे काम करते', navFeatures: 'सुविधा', navFaq: 'नेहमीचे प्रश्न', openApp: 'अॅप उघडा',
    eyebrow: 'लोकांसाठी बनवलेले. माहितीवर आधारित.',
    heroLine1: 'योग्य मदत.', heroLine2: 'पुढची वाट आता स्पष्ट.',
    heroDescription: 'सरकारी योजना समजून घेणे अवघड असू शकते. तुमची परिस्थिती तुमच्या शब्दांत सांगा. संभाव्य लाभ शोधा, महत्त्वाच्या अटी समजून घ्या आणि पुढचे पाऊल ठरवा.',
    getStarted: 'सुरुवात करा', exploreFeatures: 'सुविधा पाहा', heroNote: 'सुरुवातीला प्रोफाइल फॉर्म नको. फक्त संवाद.',
    yourLanguage: 'तुमचे शब्द. तुमची भाषा. तुमचा वेग.', languages: 'English · हिन्दी · मराठी',
    artTitle: 'थोडी स्पष्टता, पुढे जाण्यासाठी मदत.', artStep1: '०१ / सुरुवात तुमच्यापासून', artStory: 'तुमची गोष्ट सांगा', artStoryNote: 'एक संवाद, मोठी प्रश्नावली नाही.',
    artStep2: '०२ / आवश्यक माहिती जोडा', artReason: 'काय महत्त्वाचे आहे ते समजा', artStep3: '०३ / पुढे चला', artNext: 'पुढचे पाऊल, सोप्या शब्दांत.', artNextNote: 'अटी. स्रोत. स्पष्ट मार्गदर्शन.', artCaption: 'हे प्रक्रियेचे चित्रण आहे, प्रत्यक्ष पात्रतेचे निकाल नाहीत.',
    principle1: 'संवादातून सुरुवात', principle2: 'कारण स्पष्ट करणारे नियम', principle3: 'अधिकृत स्रोत आणि स्पष्ट मर्यादा',
    howEyebrow: 'कमी अंदाज. अधिक दिशा.', howTitle: '“सुरुवात कुठून करू?” ते\n“आता हे करायचे” पर्यंत.',
    howIntro: 'तुम्हाला योजनांची नावे किंवा सरकारी संज्ञा माहीत असण्याची गरज नाही. स्वतःच्या आयुष्याबद्दल माहीत असलेल्या गोष्टींपासून सुरुवात करा.',
    step1Title: 'तुमची परिस्थिती सांगा', step1Text: 'शिक्षणासाठी मदत हवी आहे? शेतीशी संबंधित लाभ शोधायचे आहेत? तुमची गरज इंग्रजी, हिंदी किंवा मराठीत सांगा.',
    step2Title: 'अपूर्ण माहिती जोडा', step2Text: 'साथी आवश्यक प्रश्न विचारतो आणि त्यांचे कारण सांगतो. AI पासून वेगळे असलेले कोडमधील नियम पात्रता तपासतात.',
    step3Title: 'पुढचे पाऊल समजून घ्या', step3Text: 'संभाव्य योजना, कागदपत्रे आणि अधिकृत स्रोत पाहा. अर्ज अडकला असेल तर रेस्क्यू मोडमधून अडथळे समजून घ्या.',
    featuresEyebrow: 'प्रश्नापासून पुढच्या पावलापर्यंत तुमचा साथी', featuresTitle: 'अवघड गोष्टी\nसमजण्यासाठी थोडी मदत.', tryWorkspace: 'वर्कस्पेस वापरून पाहा',
    discoverTitle: 'सहज संवाद. नव्या शक्यता.', discoverText: 'AI तुमच्या रोजच्या भाषेतून उपयुक्त प्रोफाइल माहिती समजून घेण्यास मदत करतो. मोठा फॉर्म न भरता सध्याच्या कॅटलॉगमधील योजनांचे सुचवलेले पर्याय पाहा.',
    illustration: 'उदाहरणादाखल संवाद', previewUser: '“मी महाराष्ट्रातील शेतकरी आहे. सुरुवात कुठून करू?”', previewAssistant: 'कॅटलॉगमधील योजनांसाठी महत्त्वाचे तपशील पाहूया. तुमच्याकडे शेतीयोग्य जमीन आहे का?', previewReason: 'हे का महत्त्वाचे: काही योजनांमध्ये जमिनीच्या मालकीची अट असते.',
    reasonTitle: 'प्रत्येक प्रश्नामागे कारण.', reasonText: 'माहिती अपुरी असेल तर विचारले जाते, अंदाज केला जात नाही. पात्रतेचे नियम आवश्यक प्रश्न निवडतात. संभाव्य जुळणारे पर्याय, अपुरी माहिती आणि प्रत्यक्ष पडताळणी वेगळी दाखवली जाते.',
    reasonBranch1: 'तुमची माहिती', reasonBranch2: 'योजनेचे नियम', reasonBranch3: 'काय माहिती बाकी आहे?', exploreSchemes: 'योजनांचे मार्गदर्शन पाहा',
    rescueLabel: 'रेस्क्यू मोड', rescueTitle: 'अर्ज अडकला आहे.\nतुम्ही अडकून राहू नका.', rescueText: 'समर्थित योजनांसाठी कागदपत्रांवरील नावांची तुलना करा, तुम्ही सांगितलेले पोर्टल संदेश समजून घ्या आणि अपूर्ण अटी शोधा. दुरुस्तीचे उपाय आणि कुठे जावे याचे मार्गदर्शन मिळवा.',
    exploreRescue: 'रेस्क्यू मोड पाहा', rescueVisualLabel: 'अडथळ्यांचे एक स्पष्ट चित्र', rescueFlow1: 'दिलेल्या कागदपत्रांच्या माहितीची तुलना', rescueFlow2: 'अपूर्ण आवश्यक अटी ओळखा', rescueFlow3: 'दुरुस्तीचे पुढचे पाऊल ठरवा', rescueDisclaimer: 'तुम्ही दिलेल्या माहितीवर आधारित. सरकारी नोंदी किंवा प्रत्यक्ष देयकाची स्थिती पडताळली जात नाही.',
    channelsEyebrow: 'मदत मिळवण्याचे आणखी मार्ग', channelsTitle: 'तुम्ही जिथे आहात,\nमदत तिथपर्यंत.', channelsIntro: 'वेब वर्कस्पेस सध्या उपलब्ध आहे. इतर माध्यमांसाठी प्रत्यक्ष सर्व्हर सेटअप आवश्यक आहे, केवळ बटण नाही.',
    whatsappBadge: 'संमतीवर आधारित बीटा', whatsappTitle: 'ओळखीचा संवाद. WhatsApp वर.', whatsappText: 'प्रशासकाने कॉन्फिगर करून पेअर केल्यावर सध्याचा WAHA ब्रिज संमतीवर आधारित मजकूर संवाद करू शकतो. अशा सेटअपवर YOJANA पाठवून सुरू करा आणि STOP पाठवून बंद करा.',
    whatsappInitial: 'उपलब्धतेसाठी सर्व्हर कॉन्फिगरेशन आवश्यक आहे.', channelDetails: 'माध्यमांचा तपशील पाहा', comingSoon: 'लवकरच येत आहे',
    voiceTitle: 'कधी कधी बोलणे सोपे असते.', voiceText: 'LiveKit एकत्रीकरणासह आवाजाद्वारे मदतीची योजना आहे. ब्राउझर कॉल आणि व्हॉइस नोट्स अद्याप उपलब्ध नाहीत.', voiceNote: 'सध्या नियोजित आहे, जोडलेले नाही. मायक्रोफोनची परवानगी मागितली जात नाही.',
    privacyEyebrow: 'तुमची माहिती. तुमची निवड.', privacyTitle: 'उपयुक्त मार्गदर्शनाची सुरुवात विश्वासाने.', privacyText: 'तुम्हाला द्यायची असेल तेवढीच माहिती द्या. आधार क्रमांक, बँक तपशील, फोन क्रमांक किंवा कागदपत्रांचे फोटो पाठवू नका. पाठवलेले संदेश सर्व्हर आणि कॉन्फिगर केलेल्या भाषा सेवेद्वारे प्रक्रिया केले जातात; सेवा प्रदात्याची लॉगिंग धोरणे लागू होऊ शकतात.',
    privacyPoint1: 'वेब वर्कस्पेस वापरून पाहण्यासाठी खाते आवश्यक नाही.', privacyPoint2: 'संवाद सत्रे तात्पुरती असून सर्व्हरच्या मेमरीत राहतात. सेवा नोंदी ठेवल्या जाऊ शकतात.', privacyPoint3: 'तुमच्या वतीने सरकारी पोर्टलवर काहीही सादर केले जात नाही.', startYourTerms: 'तुमच्या निवडीने सुरुवात करा',
    faqEyebrow: 'काही गोष्टी जाणून घेणे महत्त्वाचे', faqTitle: 'योग्य प्रश्न.\nस्पष्ट उत्तरे.', faqIntro: 'Yojana-Pilot हा स्वतंत्र मदत प्रकल्प आहे. तो कशासाठी मदत करू शकतो आणि त्याच्या मर्यादा काय आहेत ते जाणून घ्या.',
    faq1Q: 'Yojana-Pilot ही अधिकृत सरकारी सेवा आहे का?', faq1A: 'नाही. Yojana-Pilot हा स्वतंत्र प्रकल्प आहे, सरकारी पोर्टल किंवा मान्यताप्राप्त सरकारी भागीदार नाही. तो माहितीसाठी मार्गदर्शन आणि अधिकृत स्रोतांचे दुवे देतो.',
    faq2Q: 'शिफारस मिळाल्यावर माझी पात्रता निश्चित होते का?', faq2A: 'नाही. शिफारसी तुमची माहिती आणि कॅटलॉगमधील उपलब्ध नियमांवर आधारित असतात. अपूर्ण माहिती, पुनरावलोकन न झालेले तपशील किंवा अतिरिक्त अटींमुळे निकाल बदलू शकतो. अद्ययावत नियम अधिकृत सरकारी वेबसाइटवर तपासा.',
    faq3Q: 'कोणत्या योजना आणि भाषा समर्थित आहेत?', faq3A: 'तुम्ही इंग्रजी, हिंदी किंवा मराठीत लिहू शकता. व्याप्ती सध्याच्या कॅटलॉगवर अवलंबून आहे. त्यात केंद्र आणि महाराष्ट्राच्या योजना आहेत; भारतातील प्रत्येक योजनेची ही संपूर्ण यादी नाही. भाषा समजण्यासाठी कॉन्फिगर केलेली मॉडेल सेवा आवश्यक आहे.',
    faq4Q: 'रेस्क्यू मोड देयक तपासू शकतो किंवा अपील करू शकतो का?', faq4A: 'प्रत्यक्ष देयक किंवा लाभार्थी नोंदी तपासल्या जात नाहीत आणि अपील सादर केले जात नाही. कॉन्फिगर केलेल्या योजनांसाठी तुम्ही दिलेल्या पोर्टल मजकुराचा अर्थ समजावून तपासण्या आणि दुरुस्तीचे उपाय सुचवले जातात.',
    faq5Q: 'ओळखपत्रे अपलोड करावी लागतील का?', faq5A: 'नाही. कागदपत्रे अपलोड करू नका आणि ओळख किंवा खाते क्रमांक देऊ नका. रेस्क्यूमधील नावांची ऐच्छिक तुलना फक्त तुम्ही टाइप केलेल्या नावांवर होते. या सेवेला पाठवणे तुम्हाला मान्य असेल तेवढीच माहिती द्या.',
    faq6Q: 'WhatsApp किंवा कॉलद्वारे मदत मिळू शकते का?', faq6A: 'WhatsApp मजकूर मदतीसाठी प्रशासकाने कॉन्फिगर केलेला WAHA आणि तुमची संमती आवश्यक आहे. कॉन्फिगरेशनमुळे कनेक्शनची हमी मिळत नाही. LiveKit कॉल आणि व्हॉइस नोट्स अद्याप नियोजित आहेत, कार्यरत नाहीत.',
    closingEyebrow: 'एका संवादातून सुरुवात होऊ शकते.', closingTitle: 'चला, तुमचे पुढचे पाऊल शोधूया.', closingText: 'तुमचे प्रश्न विचारा. आम्ही शक्यता समजून घेण्यास मदत करू.',
    footerAbout: 'संवादातून योजनांची माहिती समजणे सोपे करणारा एक स्वतंत्र नागरी-तंत्रज्ञान प्रकल्प.', footerExplore: 'पाहा', footerWorkspace: 'नागरिक वर्कस्पेस', footerPrivacy: 'गोपनीयता आणि पर्याय', footerProject: 'प्रकल्पाबद्दल', footerSource: 'GitHub वर स्रोत ↗', footerContact: 'संपर्क / समस्या नोंदवा ↗', footerLegacy: 'जुना फॉर्म इंटरफेस', footerDisclaimer: 'स्वतंत्र मार्गदर्शन. कोणत्याही सरकारी संस्थेशी संबंधित नाही.', footerDocs: 'API दस्तऐवज ↗',
    wahaUnconfigured: 'या सर्व्हरवर कॉन्फिगर केलेले नाही.', wahaDisabled: 'कॉन्फिगर आहे, पण बंद आहे. कनेक्शन पडताळलेले नाही.', wahaEnabled: 'कॉन्फिगर आहे आणि सुरू आहे. कनेक्शन पडताळलेले नाही.', wahaUnavailable: 'स्थिती उपलब्ध नाही. अॅपमध्ये सेटअपची माहिती पाहा.',
    menuOpen: 'नेव्हिगेशन उघडा', menuClose: 'नेव्हिगेशन बंद करा', pageTitle: 'Yojana-Pilot — मदतीपर्यंत पोहोचण्याचा स्पष्ट मार्ग'
  }
};

const elements = [...document.querySelectorAll('[data-i18n]')];
const english = Object.fromEntries(elements.map(element => {
  const clone = element.cloneNode(true);
  clone.querySelectorAll('br').forEach(br => br.replaceWith('\n'));
  return [element.dataset.i18n, clone.textContent];
}));
Object.assign(english, {
  wahaUnconfigured: 'Not configured on this server.',
  wahaDisabled: 'Configured, but disabled. Connection not verified.',
  wahaEnabled: 'Configured and enabled. Connection not verified.',
  wahaUnavailable: 'Status unavailable. Check setup details in the app.',
  menuOpen: 'Open navigation', menuClose: 'Close navigation',
  pageTitle: document.title
});
translations.en = english;
const selector = document.querySelector('#site-language');
const toggle = document.querySelector('#menu-toggle');
const navigation = document.querySelector('#site-nav');
let language = 'en';
let wahaKey = null;

function setMenu(open) {
  navigation.classList.toggle('open', open);
  toggle.setAttribute('aria-expanded', String(open));
  toggle.setAttribute('aria-label', translations[language][open ? 'menuClose' : 'menuOpen']);
}

function setLanguage(value) {
  language = Object.hasOwn(translations, value) ? value : 'en';
  const copy = translations[language];
  document.documentElement.lang = language;
  document.title = copy.pageTitle;
  selector.value = language;
  elements.forEach(element => { element.textContent = copy[element.dataset.i18n] ?? english[element.dataset.i18n]; });
  if (wahaKey) document.querySelector('#public-waha-status').textContent = copy[wahaKey];
  toggle.setAttribute('aria-label', copy[toggle.getAttribute('aria-expanded') === 'true' ? 'menuClose' : 'menuOpen']);
  try { localStorage.setItem('yojana-language', language); } catch { /* Storage may be blocked. */ }
}

toggle.addEventListener('click', () => setMenu(toggle.getAttribute('aria-expanded') !== 'true'));
navigation.addEventListener('click', event => { if (event.target.closest('a')) setMenu(false); });
document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && toggle.getAttribute('aria-expanded') === 'true') { setMenu(false); toggle.focus(); }
});
document.addEventListener('click', event => {
  if (!event.target.closest('.header-inner') && toggle.getAttribute('aria-expanded') === 'true') setMenu(false);
});
matchMedia('(min-width: 901px)').addEventListener('change', () => setMenu(false));
selector.addEventListener('change', () => setLanguage(selector.value));
let savedLanguage = 'en';
try { savedLanguage = localStorage.getItem('yojana-language') || 'en'; } catch { /* Use English. */ }
setLanguage(savedLanguage);
document.querySelector('#year').textContent = new Date().getFullYear();

// Configuration is public metadata, never proof of a working WAHA connection.
async function loadChannelStatus() {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 8000);
  try {
    const response = await fetch('/api/integrations/waha/status', { signal: controller.signal });
    if (!response.ok) throw new Error('Status unavailable');
    const status = await response.json();
    wahaKey = !status.configured ? 'wahaUnconfigured' : status.enabled ? 'wahaEnabled' : 'wahaDisabled';
  } catch {
    wahaKey = 'wahaUnavailable';
  } finally {
    clearTimeout(timeout);
    document.querySelector('#public-waha-status').textContent = translations[language][wahaKey];
  }
}
loadChannelStatus();
