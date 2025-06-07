# Prompt-Based Image Gallery (프롬프트 기반 이미지 갤러리)

[![Python](https://img.shields.io/badge/Python-3.8+-blue.svg)](https://www.python.org/)
[![Tkinter](https://img.shields.io/badge/UI-CustomTkinter-brightgreen)](https://github.com/TomSchimansky/CustomTkinter)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Stable Diffusion, ComfyUI 등 AI로 생성된 이미지들을 효율적으로 관리하고 검색하기 위한 데스크톱 이미지 갤러리 애플리케이션입니다. 이미지에 포함된 프롬프트, 네거티브 프롬프트, 생성 파라미터 등 메타데이터를 자동으로 추출하여 데이터베이스에 저장하고, 이를 기반으로 강력한 검색 및 필터링 기능을 제공합니다.

![Application Screenshot]<img width="1187" alt="Image" src="https://github.com/user-attachments/assets/5b1bedea-720c-4e67-9388-a2a22e9ad0c1" />


## ✨ 주요 기능

*   **메타데이터 자동 추출 및 캐싱**:
    *   Automatic1111 WebUI 및 ComfyUI에서 생성된 PNG 이미지의 메타데이터(`parameters`, `prompt`, `workflow`)를 자동으로 파싱합니다.
    *   추출된 프롬프트, 네거티브 프롬프트, 기타 생성 정보(Seed, Steps, Sampler 등)를 SQLite 데이터베이스에 저장하여 빠른 검색을 지원합니다.

<img width="965" alt="Image" src="https://github.com/user-attachments/assets/8617d338-a3f7-4ba5-9daa-e1e72dc039d3" />


*   **강력한 검색 및 필터링**:
    *   파일 이름, 프롬프트, 태그 등 다양한 키워드로 전체 이미지를 실시간으로 검색할 수 있습니다.
    *   **즐겨찾기**, **앨범**, **태그** 시스템을 통해 이미지를 체계적으로 분류하고 필터링할 수 있습니다.
    *   특정 태그가 붙은 이미지를 갤러리에서 숨기는 필터링 기능을 지원합니다.

<img width="368" alt="Image" src="https://github.com/user-attachments/assets/2094ad35-fc05-4dcf-9948-995c85c5fd31" />

*   **다양한 보기 모드**:
    *   **일반 보기**: 썸네일 그리드 형태로 전체 이미지를 탐색합니다.
    *   **상세 보기**: 개별 이미지를 새 창에서 열어 큰 이미지와 모든 메타데이터를 확인합니다.

*   **편의 기능**:
    *   **프롬프트 번역**: Google 번역 API와 사용자 정의 사전을 통해 프롬프트를 한국어로 번역할 수 있습니다.
    *   **일괄 작업**: 여러 이미지를 동시에 선택하여 즐겨찾기에 추가하거나 태그를 일괄적으로 추가/제거할 수 있습니다.
    *   **유사 이미지 찾기**: 특정 이미지의 프롬프트를 기반으로 유사한 프롬프트를 가진 다른 이미지를 검색합니다.

<img width="445" alt="Image" src="https://github.com/user-attachments/assets/e6278d70-d4f9-4921-85cc-cb437acd96dc" />

*   **관리 도구**:
    *   앨범, 태그, 번역 사전을 손쉽게 추가, 수정, 삭제할 수 있는 통합 관리자 페이지를 제공합니다.
    *   썸네일 크기, UI 테마(라이트/다크), 이미지 폴더 등 개인화된 설정이 가능합니다.

<img width="515" alt="Image" src="https://github.com/user-attachments/assets/1d16b4d5-7e9c-4940-a8c2-e37ad593f7f2" />

<img width="516" alt="Image" src="https://github.com/user-attachments/assets/8273cb99-cb53-464a-87c5-dae09efd283f" />

## 🛠️ 설치 및 실행 방법

### 사전 요구사항

*   [Python 3.8](https://www.python.org/downloads/) 이상

### 설치 과정

1.  **저장소 클론:**
    ```bash
    git clone https://github.com/squirrel765/PromptGallery.git
    cd your-repository-name
    ```

2.  **가상 환경 생성 및 활성화:**
    ```bash
    # Windows
    python -m venv .venv
    .venv\Scripts\activate

    # macOS / Linux
    python3 -m venv .venv
    source .venv/bin/activate
    ```

3.  **필요한 라이브러리 설치:**
    ```bash
    pip install -r requirements.txt
    ```
    *`requirements.txt` 파일이 없다면 아래 명령어로 직접 설치하세요:*
    ```bash
    pip install customtkinter Pillow googletrans==4.0.0-rc1
    ```

### 실행

<img width="449" alt="Image" src="https://github.com/user-attachments/assets/dcd5054b-0a0f-436d-a057-2f35deec80f4" />


1.  **이미지 폴더 설정:**
    *   프로젝트 루트에 `images` 폴더를 만들고 관리하고 싶은 AI 이미지들을 복사해 넣습니다.
    *   또는, 프로그램을 처음 실행하고 **[설정]** 메뉴에서 원하는 이미지 폴더를 직접 지정할 수 있습니다.

2.  **애플리케이션 실행:**
    ```bash
    python app.py
    ```
    프로그램이 처음 실행되면 지정된 폴더의 이미지들을 스캔하고 메타데이터를 캐싱합니다. 이미지 수에 따라 약간의 시간이 소요될 수 있습니다.

## 📖 사용 방법

*   **이미지 탐색**: 마우스 휠 스크롤로 갤러리를 탐색하고, 썸네일을 클릭하여 상세 정보를 확인합니다.
*   **검색**: 상단 검색창에 키워드를 입력하면 실시간으로 결과가 필터링됩니다.
*   **통합 보기**: 상단의 **[통합 보기]** 버튼을 눌러 Split View 모드를 활성화/비활성화합니다.
*   **태그 추가**: 상세 보기 창에서 태그를 추가하거나, **[선택]** 모드에서 여러 이미지에 태그를 일괄 추가할 수 있습니다.
*   **앨범 관리**: 썸네일을 우클릭하여 기존 앨범에 추가하거나 새 앨범을 만들 수 있습니다. **[관리]** 메뉴에서 앨범을 편집할 수 있습니다.
*   **설정 변경**: **[설정]** 메뉴에서 썸네일 크기, 테마, 이미지 폴더 경로 등을 변경할 수 있습니다. 변경 후에는 프로그램 재시작이 필요합니다.

## 📜 라이선스

이 프로젝트는 [MIT 라이선스](LICENSE)에 따라 배포됩니다.