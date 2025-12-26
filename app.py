import streamlit as st
import tempfile
import os
import re
import base64
import time
import requests
import shutil
from bs4 import BeautifulSoup

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
            authorization_response=redirect_uri + "?code=" + code
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

# =====================================================
# 2. [강력 수정] 정보 추출 로직 (표 구조 + 텍스트 패턴 이중 검색)
# =====================================================
def extract_info_from_html_content(html_content):
    try:
        soup = BeautifulSoup(html_content, 'html.parser')
        
        # 텍스트 전체 추출 (태그 다 떼고 순수 글자만)
        full_text = soup.get_text(" ", strip=True) # 공백으로 구분
        
        회사명 = ""
        정산일자 = ""

        # -------------------------------------------------
        # [1] 회사명 추출 전략
        # -------------------------------------------------
        
        # 전략 A: 표 구조 탐색 (기존 방식 보완)
        # '상호'가 포함된 td를 찾고, 그 형제들 중 '성명'이 아닌 텍스트 찾기
        target_cells = soup.find_all(lambda tag: tag.name in ['td', 'th'] and ('상호' in tag.get_text() or '법인명' in tag.get_text()))
        for cell in target_cells:
            # 상호 칸의 바로 다음 칸들 확인
            siblings = cell.find_next_siblings(['td', 'th'])
            for sibling in siblings:
                val = sibling.get_text(strip=True)
                if not val: continue # 빈칸 패스
                
                # 라벨이 아니면 회사명으로 간주
                if not any(k in val for k in ["성명", "대표자", "등록번호", "사업자"]):
                    회사명 = val
                    break
            if 회사명: break
        
        # 전략 B: 텍스트 패턴 매칭 (백업)
        # 표 구조가 꼬여서 못 찾았을 때, "상호" ... "성명" 사이의 글자를 정규식으로 찾습니다.
        if not 회사명:
            # 패턴: 상호(또는 법인명) [공백/특수문자] [우리가 원하는 회사명] [공백] 성명(또는 대표자)
            # 예: "상호(법인명) (주)부스터스 성명(대표자)" -> "(주)부스터스" 추출
            pattern = r"(?:상호|법인명)[\s\(\):]*(.*?)[\s\(\):]*(?:성명|대표자)"
            match = re.search(pattern, full_text)
            if match:
                candidate = match.group(1).strip()
                # 너무 길면 오인식일 수 있으므로 길이 제한
                if len(candidate) < 30:
                    회사명 = candidate

        # 최종 정제
        if 회사명:
            회사명 = 회사명.replace("(", "").replace(")", "").replace("법인명", "").strip()


        # -------------------------------------------------
        # [2] 정산일자 추출 전략 (YYYY/MM/DD)
        # -------------------------------------------------
        
        # 전략 A: '작성일자' 라벨이 있는 행(TR)의 '다음 행(TR)'을 찾아서 검색 (사용자 요청)
        date_labels = soup.find_all(lambda tag: tag.name in ['td', 'th'] and ('작성' in tag.get_text() and '일자' in tag.get_text()))
        for label in date_labels:
            parent_tr = label.find_parent('tr')
            if parent_tr:
                next_tr = parent_tr.find_next_sibling('tr')
                if next_tr:
                    next_tr_text = next_tr.get_text()
                    # YYYY/MM/DD 패턴 검색
                    match = re.search(r"(\d{4})/(\d{1,2})/(\d{1,2})", next_tr_text)
                    if match:
                        y, m, d = match.groups()
                        정산일자 = f"{y}{m.zfill(2)}{d.zfill(2)}"
                        break
        
        # 전략 B: 전체 텍스트에서 YYYY/MM/DD 검색 (백업)
        # 문서 어딘가에 YYYY/MM/DD가 있다면 99% 확률로 작성일자입니다.
        if not 정산일자:
            match = re.search(r"(\d{4})/(\d{1,2})/(\d{1,2})", full_text)
            if match:
                y, m, d = match.groups()
                정산일자 = f"{y}{m.zfill(2)}{d.zfill(2)}"

        return 회사명.strip(), 정산일자

    except Exception as e:
        print(f"Parsing Error: {e}")
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
# 4. 앱 실행 로직 (다운로드 유지 기능 포함)
# =====================================================
st.set_page_config(page_title="Boosters Tax Converter", page_icon="📄")
user_email = require_login()

st.sidebar.success(f"✅ 로그인됨\n{user_email}")
if st.sidebar.button("로그아웃"):
    st.session_state.clear()
    st.rerun()

st.title("📄 세금계산서 PDF 변환기 (Boosters)")

# 세션 상태 초기화 (변환된 파일 목록 저장소)
if "processed_files" not in st.session_state:
    st.session_state.processed_files = []

uploaded_files = st.file_uploader("HTML 파일 선택 (다중 선택 가능)", type="html", accept_multiple_files=True)
biz_num = st.text_input("비밀번호 (사업자번호)", value="1828801269")

if st.button("🚀 변환 시작") and uploaded_files:
    # 기존 목록 비우기
    st.session_state.processed_files = []
    
    driver = get_driver()
    progress_bar = st.progress(0)
    
    for idx, f in enumerate(uploaded_files):
        with st.status(f"처리 중 ({idx+1}/{len(uploaded_files)}): {f.name}") as status:
            try:
                # 1. HTML 읽기
                raw_bytes = f.getvalue()
                try:
                    html_content = raw_bytes.decode('utf-8')
                except UnicodeDecodeError:
                    try:
                        html_content = raw_bytes.decode('euc-kr')
                    except:
                        html_content = raw_bytes.decode('cp949', errors='ignore')

                # 2. 정보 추출 (상호, 정산일자)
                회사명, 정산일자 = extract_info_from_html_content(html_content)
                
                # 3. 폰트 스타일 삽입
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

                # 4. 임시 파일 저장
                with tempfile.NamedTemporaryFile(delete=False, suffix=".html", mode='w', encoding='utf-8') as tmp:
                    tmp.write(html_content)
                    h_path = tmp.name

                # 5. Selenium 실행
                driver.get(f"file://{h_path}")
                wait = WebDriverWait(driver, 10)
                
                try:
                    pw = wait.until(EC.presence_of_element_located((By.XPATH, '//input[@type="password"]')))
                    pw.send_keys(biz_num)
                    driver.find_element(By.XPATH, '//button[contains(text(),"확인")]').click()
                    time.sleep(5) 
                except:
                    pass 

                # 6. PDF 생성
                pdf_data = driver.execute_cdp_cmd("Page.printToPDF", {
                    "printBackground": True,
                    "paperWidth": 8.27,
                    "paperHeight": 11.69
                })
                pdf_bytes = base64.b64decode(pdf_data["data"])
                
                # 7. 파일명 생성 로직
                if not 회사명: 회사명 = "상호확인필요"
                
                # 정산일자가 없으면 오늘 날짜 사용
                if not 정산일자:
                    now = time.localtime()
                    정산일자 = f"{now.tm_year}{str(now.tm_mon).zfill(2)}{str(now.tm_mday).zfill(2)}"
                
                safe_name = re.sub(r'[\\/*?:"<>|]', "_", 회사명)
                fn = f"세금계산서_{safe_name}_{정산일자}.pdf"
                
                # 8. 세션에 결과 저장 (다운로드 유지)
                st.session_state.processed_files.append({
                    "file_name": fn,
                    "data": pdf_bytes,
                    "original_name": f.name
                })
                
                status.update(label=f"✅ 변환 완료: {fn}", state="complete")
                os.unlink(h_path)
                
            except Exception as e:
                st.error(f"오류 ({f.name}): {str(e)}")
        
        progress_bar.progress((idx + 1) / len(uploaded_files))

    driver.quit()
    st.success("모든 변환이 완료되었습니다! 아래 목록에서 다운로드하세요.")

# 다운로드 버튼 영역 (화면 리프레시 돼도 유지됨)
if st.session_state.processed_files:
    st.write("---")
    st.subheader(f"📥 변환된 파일 목록 ({len(st.session_state.processed_files)}개)")
    
    # 모두 다운로드용 ZIP 기능은 복잡하므로 개별 다운로드 제공
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
                key=f"download_btn_{i}"
            )
            
    if st.button("목록 초기화"):
        st.session_state.processed_files = []
        st.rerun()
