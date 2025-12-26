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
# 1. Google OAuth (호환성 및 디버깅 최적화)
# =====================================================
def require_login():
    # 1. 세션에 이메일이 있으면 즉시 반환
    if "user_email" in st.session_state:
        return st.session_state["user_email"]

    # 2. 인증 코드(code) 확인
    query_params = st.query_params
    code = query_params.get("code")

    # 3. 코드가 없으면 로그인 버튼 생성
    if not code:
        # 로그인 URL 직접 생성 (라이브러리 충돌 방지)
        client_id = st.secrets["google"]["client_id"]
        redirect_uri = st.secrets["google"]["redirect_uri"]
        scope = "openid email profile"
        auth_url = (
            f"https://accounts.google.com/o/oauth2/auth?"
            f"client_id={client_id}&redirect_uri={redirect_uri}&"
            f"scope={scope}&response_type=code&access_type=offline&prompt=consent"
        )
        
        st.title("🔐 로그인 필요")
        st.info("@boosters.kr 계정으로 로그인해 주세요.")
        st.link_button("Google 계정으로 로그인", auth_url)
        st.stop()

    # 4. 토큰 교환 및 정보 획득 (requests 직접 사용)
    try:
        # (1) 토큰 교환 요청
        token_url = "https://oauth2.googleapis.com/token"
        token_data = {
            "code": code,
            "client_id": st.secrets["google"]["client_id"],
            "client_secret": st.secrets["google"]["client_secret"],
            "redirect_uri": st.secrets["google"]["redirect_uri"],
            "grant_type": "authorization_code",
        }
        
        token_resp = requests.post(token_url, data=token_data)
        token_json = token_resp.json()
        
        # 토큰 획득 실패 시 에러 출력
        if "access_token" not in token_json:
            st.error("❗ Google로부터 토큰을 받아오지 못했습니다.")
            st.json(token_json) # 구글이 보내온 실제 에러 메시지(예: redirect_uri_mismatch) 출력
            st.stop()
            
        access_token = token_json["access_token"]

        # (2) 사용자 정보 요청
        userinfo_url = "https://openidconnect.googleapis.com/v1/userinfo"
        headers = {"Authorization": f"Bearer {access_token}"}
        user_resp = requests.get(userinfo_url, headers=headers)
        userinfo = user_resp.json()
        
        email = userinfo.get("email", "").lower()

        # 도메인 체크
        if not email.endswith("@boosters.kr"):
            st.error(f"🚫 접근 권한이 없습니다: {email}")
            if st.button("다시 로그인"):
                st.query_params.clear()
                st.rerun()
            st.stop()

        # 성공 시 세션 저장 및 정리
        st.session_state["user_email"] = email
        st.query_params.clear()
        st.rerun()

    except Exception as e:
        st.error("❗ 시스템 처리 중 예상치 못한 오류가 발생했습니다.")
        st.exception(e) # 에러 상세 내용(Traceback) 출력
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
# 앱 실행 (최상단 호출)
# =====================================================
st.set_page_config(page_title="Boosters Tax Converter", page_icon="📄")

# 로그인이 완료될 때까지 이 아래 코드는 실행되지 않음
user_email = require_login()

st.sidebar.success(f"✅ 로그인됨\n{user_email}")

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
