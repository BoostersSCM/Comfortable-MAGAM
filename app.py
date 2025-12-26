import streamlit as st
import tempfile
import os
import re
import base64
import time
import json

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
# 1. Google OAuth (안정성 강화 버전)
# =====================================================
def require_login():
    # 1) 세션에 이미 이메일이 있다면 즉시 반환
    if "user_email" in st.session_state:
        return st.session_state["user_email"]

    # 2) OAuth 세션 초기화
    # redirect_uri는 구글 콘솔과 정확히 일치해야 합니다.
    oauth = OAuth2Session(
        client_id=st.secrets["google"]["client_id"],
        client_secret=st.secrets["google"]["client_secret"],
        scope="openid email profile",
        redirect_uri=st.secrets["google"]["redirect_uri"],
    )

    # 3) URL 파라미터에서 인증 코드(code) 확인
    query_params = st.query_params
    code = query_params.get("code")

    # 4) 인증 코드가 없다면 로그인 버튼 표시
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

    # 5) 토큰 교환 및 사용자 정보 획득 (에러 방지 로직 추가)
    try:
        # fetch_token 시 authorization_response 주소를 수동 조립하여 불일치 방지
        token = oauth.fetch_token(
            "https://oauth2.googleapis.com/token",
            code=code,
            authorization_response=st.secrets["google"]["redirect_uri"]
        )
        
        # 사용자 정보 요청 (token 인자를 명시적으로 전달)
        resp = oauth.get("https://openidconnect.googleapis.com/v1/userinfo", token=token)
        userinfo = resp.json()
        
        email = userinfo.get("email", "").lower()

        # 도메인 체크
        if not email.endswith("@boosters.kr"):
            st.error(f"🚫 접근 권한이 없습니다: {email}")
            if st.button("다른 계정으로 로그인"):
                st.query_params.clear()
                st.rerun()
            st.stop()

        # 세션 저장 및 정리
        st.session_state["user_email"] = email
        st.query_params.clear()  # URL에서 code 제거
        st.rerun()  # 페이지 새로고침하여 메인 화면 진입
        
        except Exception as e:
        # 실제 에러 내용을 화면에 출력하여 원인을 파악합니다.
        st.error(f"상세 에러 내용: {str(e)}") 
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
                            # 성명 앞까지만 추출
                            res_parts = parts[i + 1 :]
                            for idx, word in enumerate(res_parts):
                                if "성명" in word:
                                    res_parts = res_parts[:idx]
                                    break
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
    
    # PDF 인쇄 최적화 옵션
    options.add_argument("--kiosk-printing")

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    return driver

# =====================================================
# 4. App UI 구성
# =====================================================
st.set_page_config(page_title="Boosters Tax Converter", page_icon="📄")

# 로그인 확인
user_email = require_login()

st.sidebar.success(f"✅ 로그인됨\n{user_email}")

if st.sidebar.button("로그아웃"):
    for key in st.session_state.keys():
        del st.session_state[key]
    st.rerun()

st.title("📄 세금계산서 PDF 변환기 (Boosters)")
st.write(f"반갑습니다, **{user_email.split('@')[0]}**님! HTML 파일을 PDF로 변환합니다.")

uploaded_files = st.file_uploader(
    "HTML 파일 선택 (다중 선택 가능)",
    type="html",
    accept_multiple_files=True,
)

biz_num = st.text_input("비밀번호 (사업자번호)", value="1828801269")

# =====================================================
# 5. 실행 로직
# =====================================================
if st.button("🚀 변환 시작") and uploaded_files:
    driver = get_driver()

    for idx, uploaded_file in enumerate(uploaded_files):
        with st.status(f"처리 중: {uploaded_file.name}", expanded=False) as status:
            try:
                # 1. HTML 임시 저장
                with tempfile.NamedTemporaryFile(delete=False, suffix=".html") as tmp_html:
                    tmp_html.write(uploaded_file.getvalue())
                    html_path = tmp_html.name

                # 2. 브라우저 조작
                driver.get(f"file://{html_path}")
                wait = WebDriverWait(driver, 10)

                pw_input = wait.until(EC.presence_of_element_located((By.XPATH, '//input[@type="password"]')))
                pw_input.send_keys(biz_num)
                driver.find_element(By.XPATH, '//button[contains(text(),"확인")]').click()
                time.sleep(5)

                # 3. PDF 생성
                pdf_data = driver.execute_cdp_cmd("Page.printToPDF", {"printBackground": True})
                pdf_bytes = base64.b64decode(pdf_data["data"])

                # 4. 이름 추출을 위한 임시 저장
                with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_pdf:
                    tmp_pdf.write(pdf_bytes)
                    pdf_path = tmp_pdf.name

                회사명, 정산일자 = extract_info_from_pdf(pdf_path)
                safe_회사명 = re.sub(r'[\\/*?:"<>|]', "_", 회사명) if 회사명 else "Unknown"
                final_name = f"세금계산서_{safe_회사명}_{정산일자 or 'date'}.pdf"

                # 5. 다운로드 제공
                st.download_button(
                    label=f"📥 {final_name}",
                    data=pdf_bytes,
                    file_name=final_name,
                    mime="application/pdf",
                    key=f"download_{idx}",
                )

                status.update(label=f"✅ {final_name} 완료", state="complete")

                # 임시 파일 정리
                os.unlink(html_path)
                os.unlink(pdf_path)

            except Exception as e:
                status.update(label="❌ 실패", state="error")
                st.error(f"오류 발생 ({uploaded_file.name}): {str(e)}")

    driver.quit()
    st.balloons()
