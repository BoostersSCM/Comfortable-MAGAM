import streamlit as st
import tempfile
import os
import re
import base64
import time
import requests # 사용자 정보 요청을 위해 필수

import pdfplumber
from authlib.integrations.requests_client import OAuth2Session
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# =====================================================
# 1. Google OAuth (Python 3.13 호환성 해결판)
# =====================================================
def require_login():
    if "user_email" in st.session_state:
        return st.session_state["user_email"]

    # OAuth 세션 초기화
    oauth = OAuth2Session(
        client_id=st.secrets["google"]["client_id"],
        client_secret=st.secrets["google"]["client_secret"],
        scope="openid email profile",
        redirect_uri=st.secrets["google"]["redirect_uri"],
    )

    query_params = st.query_params
    code = query_params.get("code")

    # 인증 코드가 없으면 로그인 버튼 표시
    if not code:
        auth_url, _ = oauth.create_authorization_url(
            "https://accounts.google.com/o/oauth2/auth",
            access_type="offline",
            prompt="consent",
        )
        st.title("🔐 로그인 필요")
        st.info("@boosters.kr 계정으로 로그인해 주세요.")
        st.link_button("Google 계정으로 로그인", auth_url)
        st.stop()

    # 토큰 교환 및 사용자 정보 획득
    try:
        # 1) 토큰 가져오기
        token = oauth.fetch_token(
            "https://oauth2.googleapis.com/token",
            code=code,
            # redirect_uri 불일치 방지를 위해 secrets 값 명시
            authorization_response=st.secrets["google"]["redirect_uri"] + "?code=" + code
        )

        # 2) TypeError 해결: oauth.get 대신 requests.get 직접 사용
        # 에러가 발생하던 'token=token' 인자 전달 방식을 우회합니다.
        userinfo_endpoint = "https://openidconnect.googleapis.com/v1/userinfo"
        headers = {'Authorization': f"Bearer {token['access_token']}"}
        resp = requests.get(userinfo_endpoint, headers=headers)
        userinfo = resp.json()

        email = userinfo.get("email", "").lower()

        # 도메인 체크
        if not email.endswith("@boosters.kr"):
            st.error(f"🚫 접근 권한이 없습니다: {email}")
            st.stop()

        # 세션 저장 및 정리
        st.session_state["user_email"] = email
        st.query_params.clear() 
        st.rerun()
        
    except Exception as e:
        st.error(f"인증 처리 중 오류가 발생했습니다: {str(e)}")
        if st.button("다시 로그인 시도"):
            st.query_params.clear()
            st.rerun()
        st.stop()

# =====================================================
# 2. PDF 정보 추출 및 기타 로직 (기존 유지)
# =====================================================
def extract_info_from_pdf(pdf_path):
    try:
        with pdfplumber.open(pdf_path) as pdf:
            text = pdf.pages[0].extract_text()
            lines = text.split("\n")
            회사명 = ""
            for line in lines:
                if "상호" in line or "법인명" in line:
                    parts = line.split()
                    for i, p in enumerate(parts):
                        if ("상호" in p or "법인명" in p) and i + 1 < len(parts):
                            res_parts = parts[i + 1 :]
                            for idx, word in enumerate(res_parts):
                                if "성명" in word: res_parts = res_parts[:idx]; break
                            회사명 = " ".join(res_parts)
                            break
                    break
            정산일자 = ""
            date_pattern = r"(\d{4})[년\s]*(\d{1,2})[월\s]*(\d{1,2})[일\s]*"
            matches = re.findall(date_pattern, text)
            if matches:
                y, m, d = matches[0]
                정산일자 = f"{y}{m.zfill(2)}{d.zfill(2)}"
            return 회사명.strip(), 정산일자
    except: return "", ""

# =====================================================
# 3. Selenium Driver 설정 (Streamlit Cloud 호환 수정판)
# =====================================================
def get_driver():
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    
    # 중요: Streamlit Cloud 환경에 설치된 크롬 위치 지정
    options.binary_location = "/usr/bin/chromium"

    # 중요: 버전 충돌 방지를 위해 webdriver_manager 대신 시스템 드라이버 직접 지정
    # packages.txt에 의해 설치된 경로입니다.
    service = Service("/usr/bin/chromedriver")
    
    driver = webdriver.Chrome(service=service, options=options)
    return driver

# --- 앱 실행 ---
st.set_page_config(page_title="Boosters Tax Converter", page_icon="📄")
user_email = require_login()

st.sidebar.success(f"✅ 로그인됨\n{user_email}")
if st.sidebar.button("로그아웃"):
    st.session_state.clear()
    st.rerun()

st.title("📄 세금계산서 PDF 변환기 (Boosters)")
uploaded_files = st.file_uploader("HTML 파일 선택", type="html", accept_multiple_files=True)
biz_num = st.text_input("비밀번호 (사업자번호)", value="1828801269")

if st.button("🚀 변환 시작") and uploaded_files:
    driver = get_driver()
    for idx, f in enumerate(uploaded_files):
        with st.status(f"처리 중: {f.name}") as status:
            try:
                with tempfile.NamedTemporaryFile(delete=False, suffix=".html") as tmp:
                    tmp.write(f.getvalue())
                    h_path = tmp.name
                driver.get(f"file://{h_path}")
                wait = WebDriverWait(driver, 10)
                pw = wait.until(EC.presence_of_element_located((By.XPATH, '//input[@type="password"]')))
                pw.send_keys(biz_num)
                driver.find_element(By.XPATH, '//button[contains(text(),"확인")]').click()
                time.sleep(5)
                pdf_data = driver.execute_cdp_cmd("Page.printToPDF", {"printBackground": True})
                pdf_bytes = base64.b64decode(pdf_data["data"])
                with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_pdf:
                    tmp_pdf.write(pdf_bytes)
                    p_path = tmp_pdf.name
                name, dt = extract_info_from_pdf(p_path)
                safe_name = re.sub(r'[\\/*?:"<>|]', "_", name) if name else "Unknown"
                fn = f"세금계산서_{safe_name}_{dt or 'date'}.pdf"
                st.download_button(label=f"📥 {fn}", data=pdf_bytes, file_name=fn, mime="application/pdf", key=f"d_{idx}")
                status.update(label="✅ 완료", state="complete")
                os.unlink(h_path); os.unlink(p_path)
            except Exception as e: st.error(str(e))
    driver.quit()
    st.balloons()
