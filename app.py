import streamlit as st
import tempfile
import os
import re
import base64
import time
import requests
import shutil

import pdfplumber
from authlib.integrations.requests_client import OAuth2Session
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# =====================================================
# 1. Google OAuth (기존 유지)
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

    code = st.query_params.get("code")

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

    try:
        token = oauth.fetch_token(
            "https://oauth2.googleapis.com/token",
            code=code,
            authorization_response=st.secrets["google"]["redirect_uri"] + "?code=" + code
        )

        userinfo_endpoint = "https://openidconnect.googleapis.com/v1/userinfo"
        headers = {'Authorization': f"Bearer {token['access_token']}"}
        resp = requests.get(userinfo_endpoint, headers=headers)
        userinfo = resp.json()
        email = userinfo.get("email", "").lower()

        if not email.endswith("@boosters.kr"):
            st.error(f"🚫 접근 권한이 없습니다: {email}")
            st.stop()

        st.session_state["user_email"] = email
        st.query_params.clear()
        st.rerun()
        
    except Exception as e:
        st.error(f"인증 처리 중 오류가 발생했습니다: {str(e)}")
        if st.button("다시 로그인 시도"):
            st.query_params.clear()
            st.rerun()
        st.stop()

        return "", ""

# =====================================================
# 3. Selenium 설정 (서버 내장 크롬 사용)
# =====================================================
def get_driver():
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--lang=ko_KR") # 한글 로케일 강제 설정

    # fonts-nanum이 설치되어 있어야 한글이 나옵니다.
    options.binary_location = "/usr/bin/chromium"
    service = Service("/usr/bin/chromedriver")
    
    driver = webdriver.Chrome(service=service, options=options)
    return driver

# =====================================================
# 4. 앱 실행 로직
# =====================================================
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
                # [수정] HTML 인코딩 보정 로직
                raw_bytes = f.getvalue()
                
                # 1. 인코딩 감지 및 디코딩 시도 (EUC-KR 대응)
                try:
                    html_content = raw_bytes.decode('utf-8')
                except UnicodeDecodeError:
                    try:
                        html_content = raw_bytes.decode('euc-kr')
                    except:
                        html_content = raw_bytes.decode('cp949', errors='ignore')

                # 2. 메타 태그 강제 삽입 (깨짐 방지 핵심)
                if '<meta charset="utf-8">' not in html_content.lower():
                    html_content = '<meta charset="utf-8">\n' + html_content

                # 3. UTF-8로 다시 저장
                with tempfile.NamedTemporaryFile(delete=False, suffix=".html", mode='w', encoding='utf-8') as tmp:
                    tmp.write(html_content)
                    h_path = tmp.name

                # Selenium 실행
                driver.get(f"file://{h_path}")
                wait = WebDriverWait(driver, 10)
                
                # 비밀번호 입력
                try:
                    pw = wait.until(EC.presence_of_element_located((By.XPATH, '//input[@type="password"]')))
                    pw.send_keys(biz_num)
                    driver.find_element(By.XPATH, '//button[contains(text(),"확인")]').click()
                    time.sleep(5) # 렌더링 대기
                except:
                    pass # 비밀번호 없는 경우 통과

                # PDF 생성
                pdf_data = driver.execute_cdp_cmd("Page.printToPDF", {
                    "printBackground": True,
                    "paperWidth": 8.27, # A4
                    "paperHeight": 11.69
                })
                pdf_bytes = base64.b64decode(pdf_data["data"])

                # 임시 PDF 저장 및 정보 추출
                with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_pdf:
                    tmp_pdf.write(pdf_bytes)
                    p_path = tmp_pdf.name
                
                회사명, 정산일자 = extract_info_from_pdf(p_path)
                
                # 폰트 문제로 추출 실패 시 대비
                if not 회사명:
                    회사명 = "확인필요"
                
                safe_name = re.sub(r'[\\/*?:"<>|]', "_", 회사명)
                fn = f"세금계산서_{safe_name}_{정산일자}.pdf" if 정산일자 else f"세금계산서_{safe_name}_{int(time.time())}.pdf"
                
                # 다운로드 버튼
                st.download_button(label=f"📥 {fn}", data=pdf_bytes, file_name=fn, mime="application/pdf", key=f"d_{idx}")
                status.update(label="✅ 완료", state="complete")
                
                # 파일 정리
                os.unlink(h_path)
                os.unlink(p_path)
                
            except Exception as e:
                st.error(f"오류: {str(e)}")
                
    driver.quit()
    st.balloons()
