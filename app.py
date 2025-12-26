import streamlit as st
import tempfile
import os
import re
import base64
import time
import requests

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
# 1. Google OAuth (Python 3.13 호환성 수정판)
# =====================================================
def require_login():
    if "user_email" in st.session_state:
        return st.session_state["user_email"]

    # 1. Secrets 섹션 이름 확인 (사용자가 설정한 이름에 맞춰 수정하세요)
    # 만약 Secrets에 [google_auth]라고 적었다면 "google"을 "google_auth"로 바꿔야 합니다.
    secret_key = "google" # 또는 "google_auth"
    
    try:
        oauth = OAuth2Session(
            client_id=st.secrets[secret_key]["client_id"],
            client_secret=st.secrets[secret_key]["client_secret"],
            scope="openid email profile",
            redirect_uri=st.secrets[secret_key]["redirect_uri"],
        )
    except KeyError as e:
        st.error(f"❌ Secrets 설정 오류: {secret_key} 섹션에 {e} 키가 없습니다.")
        st.stop()

    query_params = st.query_params
    code = query_params.get("code")

    if not code:
        auth_url, _ = oauth.create_authorization_url(
            "https://accounts.google.com/o/oauth2/auth",
            access_type="offline",
            prompt="consent",
        )
        st.title("🔐 로그인 필요")
        st.link_button("Google 계정으로 로그인", auth_url)
        st.stop()

    try:
        # 토큰 획득 시도
        token = oauth.fetch_token(
            "https://oauth2.googleapis.com/token",
            code=code,
            client_secret=st.secrets[secret_key]["client_secret"]
        )

        # 사용자 정보 획득 시도
        userinfo_endpoint = "https://openidconnect.googleapis.com/v1/userinfo"
        headers = {'Authorization': f"Bearer {token['access_token']}"}
        userinfo_resp = requests.get(userinfo_endpoint, headers=headers)
        userinfo = userinfo_resp.json()

        email = userinfo.get("email", "").lower()

        if not email.endswith("@boosters.kr"):
            st.error(f"🚫 접근 권한이 없습니다: {email}")
            st.stop()

        st.session_state["user_email"] = email
        st.query_params.clear() 
        st.rerun()
        
    except Exception as e:
        # ⚠️ 이 부분이 핵심입니다. 어떤 에러인지 상세히 출력합니다.
        st.error("❗ 인증 과정에서 상세 에러가 발생했습니다.")
        st.exception(e) # 전체 에러 트레이스백 출력
        if st.button("로그인 다시 시도"):
            st.query_params.clear()
            st.rerun()
        st.stop()

# =====================================================
# 2. PDF 정보 추출 (기존 로직 유지)
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
                            회사명 = " ".join(parts[i + 1 :])
                            break
                    break
            정산일자 = ""
            date_pattern = r"(\d{4})[년\s]*(\d{1,2})[월\s]*(\d{1,2})[일\s]*"
            matches = re.findall(date_pattern, text)
            if matches:
                y, m, d = matches[0]
                정산일자 = f"{y}{m.zfill(2)}{d.zfill(2)}"
            return 회사명.strip(), 정산일자
    except Exception:
        return "", ""

# =====================================================
# 3. Selenium Driver 설정
# =====================================================
def get_driver():
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    service = Service(ChromeDriverManager().install())
    return webdriver.Chrome(service=service, options=options)

# =====================================================
# 4. 앱 실행 및 UI
# =====================================================
st.set_page_config(page_title="Boosters Tax Converter", page_icon="📄")

# 로그인 강제
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
    for idx, uploaded_file in enumerate(uploaded_files):
        with st.status(f"처리 중: {uploaded_file.name}") as status:
            try:
                with tempfile.NamedTemporaryFile(delete=False, suffix=".html") as tmp:
                    tmp.write(uploaded_file.getvalue())
                    html_path = tmp.name
                driver.get(f"file://{html_path}")
                wait = WebDriverWait(driver, 10)
                pw_input = wait.until(EC.presence_of_element_located((By.XPATH, '//input[@type="password"]')))
                pw_input.send_keys(biz_num)
                driver.find_element(By.XPATH, '//button[contains(text(),"확인")]').click()
                time.sleep(5)
                pdf_data = driver.execute_cdp_cmd("Page.printToPDF", {"printBackground": True})
                pdf_bytes = base64.b64decode(pdf_data["data"])
                with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_pdf:
                    tmp_pdf.write(pdf_bytes)
                    pdf_path = tmp_pdf.name
                회사명, 정산일자 = extract_info_from_pdf(pdf_path)
                safe_회사명 = re.sub(r'[\\/*?:"<>|]', "_", 회사명) if 회사명 else "Unknown"
                final_name = f"세금계산서_{safe_회사명}_{정산일자 or 'date'}.pdf"
                st.download_button(label=f"📥 {final_name}", data=pdf_bytes, file_name=final_name, mime="application/pdf", key=f"dl_{idx}")
                status.update(label="✅ 완료", state="complete")
                os.unlink(html_path)
                os.unlink(pdf_path)
            except Exception as e:
                st.error(f"오류: {str(e)}")
    driver.quit()
    st.balloons()
