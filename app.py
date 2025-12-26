import streamlit as st
import tempfile
import os
import re
import base64
import time
import requests
import shutil
import zipfile  # [추가] 압축 기능을 위한 라이브러리
import io       # [추가] 메모리 상에서 파일을 다루기 위한 라이브러리

import pdfplumber
from authlib.integrations.requests_client import OAuth2Session
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# =====================================================
# 1. Google OAuth
# =====================================================
def require_login():
    if "user_email" in st.session_state:
        return st.session_state["user_email"]

    client_id = st.secrets["google"]["client_id"]
    client_secret = st.secrets["google"]["client_secret"]
    redirect_uri = st.secrets["google"]["redirect_uri"]

    oauth = OAuth2Session(
        client_id=client_id,
        client_secret=client_secret,
        scope="openid email profile",
        redirect_uri=redirect_uri,
    )

    code = st.query_params.get("code")

    if not code:
        # prompt="consent" 삭제 -> 자동 로그인 활성화
        auth_url, _ = oauth.create_authorization_url(
            "https://accounts.google.com/o/oauth2/auth",
            access_type="offline",
        )
        st.title("🔐 로그인 필요")
        st.info("Boosters 계정으로 로그인해 주세요.")
        st.link_button("Boosters 계정으로 로그인", auth_url)
        st.stop()

    try:
        token = oauth.fetch_token(
            "https://oauth2.googleapis.com/token",
            code=code,
            authorization_response=redirect_uri + "?code=" + code
        )

        headers = {'Authorization': f"Bearer {token['access_token']}"}
        resp = requests.get("https://openidconnect.googleapis.com/v1/userinfo", headers=headers)
        userinfo = resp.json()
        email = userinfo.get("email", "").lower()

        if not email.endswith("@boosters.kr"):
            st.error(f"🚫 접근 권한이 없습니다: {email}")
            st.stop()

        st.session_state["user_email"] = email
        st.query_params.clear()
        st.rerun()
        
    except Exception as e:
        st.error(f"인증 오류: {str(e)}")
        if st.button("다시 로그인"):
            st.query_params.clear()
            st.rerun()
        st.stop()

# =====================================================
# 2. PDF 정보 추출 (텍스트 기반)
# =====================================================
def extract_info_from_pdf(pdf_path):
    try:
        with pdfplumber.open(pdf_path) as pdf:
            text = pdf.pages[0].extract_text()
            if not text: return "", ""
            
            회사명 = ""
            정산일자 = ""
            
            # 1. 회사명 추출 ('상호' ~ '성명' 사이)
            name_pattern = r"(?:상호|법인명)[^\s]*\s+(.*?)\s+(?:성명|대표자)"
            match = re.search(name_pattern, text)
            if match:
                회사명 = match.group(1).strip()
            
            # 백업 로직
            if not 회사명:
                lines = text.split('\n')
                for line in lines:
                    if "상호" in line and "성명" in line:
                        temp = line.split("성명")[0]
                        if "상호" in temp:
                            회사명 = temp.split("상호")[-1]
                            회사명 = 회사명.replace("(법인명)", "").replace("(", "").replace(")", "").strip()
                            break

            # 2. 날짜 추출 (YYYY.MM.DD 등)
            date_match = re.search(r"(\d{4})[\.\-/](\d{1,2})[\.\-/](\d{1,2})", text)
            if date_match:
                y, m, d = date_match.groups()
                정산일자 = f"{y}{m.zfill(2)}{d.zfill(2)}"

            return 회사명.strip(), 정산일자

    except Exception as e:
        return "", ""

# =====================================================
# 3. Selenium 설정
# =====================================================
def get_driver():
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--lang=ko_KR") 

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

if "processed_files" not in st.session_state:
    st.session_state.processed_files = []

uploaded_files = st.file_uploader("HTML 파일 선택", type="html", accept_multiple_files=True)
biz_num = st.text_input("비밀번호 (사업자번호)", value="1828801269")

if st.button("🚀 변환 시작") and uploaded_files:
    st.session_state.processed_files = []
    driver = get_driver()
    progress_bar = st.progress(0)
    
    for idx, f in enumerate(uploaded_files):
        with st.status(f"처리 중 ({idx+1}/{len(uploaded_files)}): {f.name}") as status:
            try:
                raw_bytes = f.getvalue()
                try:
                    html_content = raw_bytes.decode('utf-8')
                except UnicodeDecodeError:
                    try:
                        html_content = raw_bytes.decode('euc-kr')
                    except:
                        html_content = raw_bytes.decode('cp949', errors='ignore')

                font_style = """
                <style>
                    @import url('https://fonts.googleapis.com/css2?family=Nanum+Gothic:wght@400;700&display=swap');
                    body, table, td, span, div, p, input { 
                        font-family: 'NanumGothic', 'Nanum Gothic', 'Malgun Gothic', sans-serif !important; 
                    }
                </style>
                <meta charset="utf-8">
                """
                if "<head>" in html_content.lower():
                    html_content = html_content.replace("<head>", "<head>" + font_style, 1)
                else:
                    html_content = font_style + html_content

                with tempfile.NamedTemporaryFile(delete=False, suffix=".html", mode='w', encoding='utf-8') as tmp:
                    tmp.write(html_content)
                    h_path = tmp.name

                driver.get(f"file://{h_path}")
                wait = WebDriverWait(driver, 10)
                try:
                    pw = wait.until(EC.presence_of_element_located((By.XPATH, '//input[@type="password"]')))
                    pw.send_keys(biz_num)
                    driver.find_element(By.XPATH, '//button[contains(text(),"확인")]').click()
                    time.sleep(5) 
                except:
                    pass 

                pdf_data = driver.execute_cdp_cmd("Page.printToPDF", {
                    "printBackground": True,
                    "paperWidth": 8.27, "paperHeight": 11.69
                })
                pdf_bytes = base64.b64decode(pdf_data["data"])
                
                with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_pdf:
                    tmp_pdf.write(pdf_bytes)
                    p_path = tmp_pdf.name
                
                회사명, 정산일자 = extract_info_from_pdf(p_path)
                
                if not 회사명: 회사명 = "상호확인필요"
                if not 정산일자: 
                    now = time.localtime()
                    정산일자 = f"{now.tm_year}{str(now.tm_mon).zfill(2)}{str(now.tm_mday).zfill(2)}"

                safe_name = re.sub(r'[\\/*?:"<>|]', "_", 회사명)
                fn = f"세금계산서_{safe_name}_{정산일자}.pdf"
                
                st.session_state.processed_files.append({
                    "file_name": fn,
                    "data": pdf_bytes
                })
                
                status.update(label=f"✅ 완료: {fn}", state="complete")
                os.unlink(h_path)
                os.unlink(p_path)
                
            except Exception as e:
                st.error(f"오류 ({f.name}): {str(e)}")
        
        progress_bar.progress((idx + 1) / len(uploaded_files))

    driver.quit()
    st.success("변환 완료!")

# =====================================================
# 5. 다운로드 영역 (일괄 다운로드 추가)
# =====================================================
if st.session_state.processed_files:
    st.write("---")
    
    # [추가됨] 파일이 2개 이상일 때만 ZIP 다운로드 버튼 표시
    if len(st.session_state.processed_files) > 1:
        st.subheader("📦 일괄 다운로드")
        
        # 메모리에 ZIP 파일 생성
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w") as zip_file:
            for file_info in st.session_state.processed_files:
                # ZIP 파일 내에 PDF 추가
                zip_file.writestr(file_info["file_name"], file_info["data"])
        
        st.download_button(
            label="📦 모든 파일 압축 다운로드 (ZIP)",
            data=zip_buffer.getvalue(),
            file_name=f"세금계산서_모음_{int(time.time())}.zip",
            mime="application/zip",
            type="primary" # 버튼 색상 강조
        )
        st.write("---")

    st.subheader(f"📥 개별 다운로드 ({len(st.session_state.processed_files)}개)")
    
    for i, file_info in enumerate(st.session_state.processed_files):
        col1, col2 = st.columns([3, 1])
        with col1:
            st.write(f"**{i+1}.** {file_info['file_name']}")
        with col2:
            st.download_button(
                label="다운로드",
                data=file_info["data"],
                file_name=file_info["file_name"],
                mime="application/pdf",
                key=f"dl_{i}"
            )
            
    if st.button("초기화"):
        st.session_state.processed_files = []
        st.rerun()
