import streamlit as st
import tempfile
import os
import re
import base64
import time

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
# 1. Google OAuth (Streamlit Cloud 안정판)
# =====================================================
def require_login():
    if "user_email" in st.session_state:
        return st.session_state["user_email"]

    oauth = OAuth2Session(
        client_id=st.secrets["google"]["client_id"],
        client_secret=st.secrets["google"]["client_secret"],
        scope="openid email profile",
        redirect_uri=st.secrets["google"]["redirect_uri"],
    )

    query_params = st.query_params

    if "code" not in query_params:
        auth_url, _ = oauth.create_authorization_url(
            "https://accounts.google.com/o/oauth2/auth",
            access_type="offline",
            prompt="consent",
        )
        st.title("🔐 로그인 필요")
        st.link_button("Google 계정으로 로그인", auth_url)
        st.stop()

    token = oauth.fetch_token(
        "https://oauth2.googleapis.com/token",
        authorization_response=st.secrets["google"]["redirect_uri"] + "?code=" + query_params["code"],
    )

    userinfo = oauth.get(
        "https://openidconnect.googleapis.com/v1/userinfo",
        token=token,
    ).json()

    email = userinfo.get("email", "").lower()

    if not email.endswith("@boosters.kr"):
        st.error(f"🚫 접근 권한이 없습니다: {email}")
        st.stop()

    st.session_state["user_email"] = email
    st.query_params.clear()  # code 제거
    return email


# =====================================================
# 2. PDF 정보 추출
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
                        if p in ("상호", "법인명") and i + 1 < len(parts):
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
# 3. Selenium Driver (Cloud Headless)
# =====================================================
def get_driver():
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    return driver


# =====================================================
# 4. App Start
# =====================================================
st.set_page_config(
    page_title="Boosters Tax Converter",
    page_icon="📄",
)

user_email = require_login()

st.sidebar.success(f"✅ 로그인됨\n{user_email}")

if st.sidebar.button("로그아웃"):
    st.session_state.clear()
    st.experimental_rerun()

st.title("📄 세금계산서 PDF 변환기 (Boosters)")
st.write("HTML 세금계산서를 PDF로 변환합니다.")

uploaded_files = st.file_uploader(
    "HTML 파일 선택 (다중 선택 가능)",
    type="html",
    accept_multiple_files=True,
)

biz_num = st.text_input(
    "비밀번호 (사업자번호)",
    value="1828801269",
)

# =====================================================
# 5. Main Logic
# =====================================================
if st.button("🚀 변환 시작") and uploaded_files:
    driver = get_driver()

    for idx, uploaded_file in enumerate(uploaded_files):
        with st.status(f"처리 중: {uploaded_file.name}", expanded=False) as status:
            try:
                with tempfile.NamedTemporaryFile(delete=False, suffix=".html") as tmp_html:
                    tmp_html.write(uploaded_file.getvalue())
                    html_path = tmp_html.name

                driver.get(f"file://{html_path}")
                wait = WebDriverWait(driver, 10)

                pw_input = wait.until(
                    EC.presence_of_element_located((By.XPATH, '//input[@type="password"]'))
                )
                pw_input.send_keys(biz_num)

                driver.find_element(
                    By.XPATH, '//button[contains(text(),"확인")]'
                ).click()

                time.sleep(5)

                pdf_data = driver.execute_cdp_cmd(
                    "Page.printToPDF",
                    {"printBackground": True}
                )

                pdf_bytes = base64.b64decode(pdf_data["data"])

                with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_pdf:
                    tmp_pdf.write(pdf_bytes)
                    pdf_path = tmp_pdf.name

                회사명, 정산일자 = extract_info_from_pdf(pdf_path)
                safe_회사명 = re.sub(r'[\\/*?:"<>|]', "_", 회사명) if 회사명 else "Unknown"

                final_name = f"세금계산서_{safe_회사명}_{정산일자 or 'date'}.pdf"

                st.download_button(
                    label=f"📥 {final_name}",
                    data=pdf_bytes,
                    file_name=final_name,
                    mime="application/pdf",
                    key=f"download_{idx}",
                )

                status.update(label="✅ 완료", state="complete")

                os.unlink(html_path)
                os.unlink(pdf_path)

            except Exception as e:
                status.update(label="❌ 실패", state="error")
                st.error(str(e))

    driver.quit()
    st.balloons()
