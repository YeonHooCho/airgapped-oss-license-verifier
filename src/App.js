import React, { useState, useEffect, useRef } from 'react';
import { 
  Card, Row, Col, Typography, Tag, Divider, Button, Alert, 
  List, Form, Input, Select, Upload, message, Modal, Progress, Tabs, Space, Table
} from 'antd';
import { 
  WarningOutlined, BulbOutlined, FilePdfOutlined, CheckCircleOutlined, 
  InfoCircleOutlined, DeploymentUnitOutlined, UserOutlined, LockOutlined, 
  InboxOutlined, GithubOutlined, ArrowLeftOutlined, SafetyCertificateOutlined,
  CodeOutlined, BugOutlined, UserAddOutlined, ClockCircleOutlined, FileExclamationOutlined,
  SyncOutlined, LogoutOutlined, FileExcelOutlined, FileTextOutlined,
  CheckOutlined, CloseCircleOutlined, DatabaseOutlined, BranchesOutlined,
  ExclamationCircleOutlined, LinkOutlined
} from '@ant-design/icons';
import { PieChart, Pie, Cell, ResponsiveContainer } from 'recharts';

const { Title, Text, Paragraph } = Typography;
const { TabPane } = Tabs;
const { Dragger } = Upload;
const { Option } = Select;

const App = () => {
  // 화면 단계: 'login' | 'signup' | 'upload' | 'dashboard'
  const [currentStep, setCurrentStep] = useState('login');
  const [currentUser, setCurrentUser] = useState('admin@hongik.ac.kr');
  
  const [projectTarget, setProjectTarget] = useState('commercial');
  const [repoUrl, setRepoUrl] = useState('');
  const [uploadedFile, setUploadedFile] = useState(null);
  const [fileContent, setFileContent] = useState('');

  // 실제 백엔드 분석 결과 저장 상태
  const [analysisResult, setAnalysisResult] = useState(null);

  // 모달 상태 및 동적 텍스트 관리
  const [analyzingModal, setAnalyzingModal] = useState(false);
  const [analysisProgress, setAnalysisProgress] = useState(0);
  const [analysisStatusText, setAnalysisStatusText] = useState('1/3단계: 소스코드 AST/Tree-sitter 구문 파싱 중...');
  const [analysisSubText, setAnalysisSubText] = useState('FastAPI 백엔드 엔진이 구문 트리 파싱 및 다국어 패키지를 추출 중입니다.');
  const [totalEstimatedSec, setTotalEstimatedSec] = useState(15);
  const [remainingSec, setRemainingSec] = useState(15);
  const [elapsedSec, setElapsedSec] = useState(0);

  // 선택된 테이블 행(파일)을 기억하는 상태
  const [selectedRowFile, setSelectedRowFile] = useState(null);

  const timerRef = useRef(null);

  const handleLogout = () => {
    message.info('로그아웃되었습니다.');
    setSelectedRowFile(null);
    setCurrentStep('login');
  };

  const handleLogin = (values) => {
    setCurrentUser(values.email);
    message.success(`환영합니다! [${values.email}] 로그인 성공`);
    setCurrentStep('upload');
  };

  const handleSignup = (values) => {
    setCurrentUser(values.userId);
    message.success(`회원가입이 완료되었습니다! [${values.userId}] 계정으로 로그인해 주세요.`);
    setCurrentStep('login');
  };

  const calculateActualEstimatedTime = (file, githubUrl) => {
    if (githubUrl && githubUrl.trim().length > 0) return 20;
    if (file) {
      const fileName = file.name.toLowerCase();
      if (fileName.endsWith('.zip')) return 25;
      return 12;
    }
    return 15;
  };

  const handleStartAnalysis = async () => {
    if (!uploadedFile) {
      message.warning('분석할 소스코드 또는 ZIP 파일을 먼저 등록해주세요!');
      return;
    }

    const estimatedTime = calculateActualEstimatedTime(uploadedFile, repoUrl);
    setTotalEstimatedSec(estimatedTime);
    setRemainingSec(estimatedTime);
    setElapsedSec(0);
    setAnalysisProgress(10);
    setSelectedRowFile(null);
    
    setAnalysisStatusText('1/3단계: 소스코드 AST & Tree-sitter 구문 파싱 중...');
    setAnalysisSubText('FastAPI 백엔드 엔진이 4개 언어 구문 파싱 및 패키지를 추출 중입니다.');
    setAnalyzingModal(true);

    const startTime = Date.now();

    timerRef.current = setInterval(() => {
      const secondsPassed = Math.floor((Date.now() - startTime) / 1000);
      const secondsLeft = Math.max(1, estimatedTime - secondsPassed);

      setElapsedSec(secondsPassed);
      setRemainingSec(secondsLeft);

      const calculatedPercent = Math.min(90, Math.round((secondsPassed / estimatedTime) * 100));
      setAnalysisProgress(calculatedPercent);

      if (calculatedPercent < 35) {
        setAnalysisStatusText('1/3단계: 소스코드 Tree-sitter 구문 파싱 및 2-Pass 상속 추론 중...');
        setAnalysisSubText('프로젝트 루트 라이선스 식별 및 서브 컴포넌트 상속 체인을 형성합니다.');
      } else if (calculatedPercent < 70) {
        setAnalysisStatusText('2/3단계: OSADL 라이선스 충돌 매트릭스 & 의존성 DB 대조 중...');
        setAnalysisSubText('조합된 라이선스 간 법적 양립 가능성(Compatibility)을 검증 중입니다.');
      } else {
        setAnalysisStatusText('3/3단계: 로컬 LLM(Ollama Qwen 2.5) 기반 심층 판정 중...');
        setAnalysisSubText('폐쇄망 AI 모델이 소스코드 문맥과 안전 가드레일을 종합 판정 중입니다.');
      }
    }, 1000);

    try {
      const formData = new FormData();
      formData.append('file', uploadedFile);

      const response = await fetch('http://localhost:8080/api/upload', {
        method: 'POST',
        body: formData,
      });

      if (!response.ok) {
        throw new Error(`서버 응답 오류 (HTTP ${response.status})`);
      }

      const resultData = await response.json();
      setAnalysisResult(resultData);

      clearInterval(timerRef.current);
      setAnalysisProgress(100);
      setRemainingSec(0);
      setAnalysisStatusText('분석 완료! 결과 대시보드로 이동합니다.');
      setAnalysisSubText('2-Pass 상속 및 OSADL 충돌 매트릭스 리포트를 생성했습니다.');

      setTimeout(() => {
        setAnalyzingModal(false);
        setCurrentStep('dashboard');
        message.success('분석이 성공적으로 완료되었습니다!');
      }, 500);

    } catch (err) {
      clearInterval(timerRef.current);
      setAnalyzingModal(false);
      console.error(err);
      message.error(`분석 처리 실패: ${err.message}. 백엔드 서버(8080/8000)를 확인해주세요.`);
    }
  };

  useEffect(() => {
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, []);

  // -------------------------------------------------------------
  // [동적 데이터 연산: 2-Pass 상속 엔진 & OSADL 충돌 매트릭스 반영]
  // -------------------------------------------------------------
  const isZip = analysisResult?.input_type === 'zip';
  
  // 1) 2-Pass 상속 엔진의 대표 루트 라이선스 우선 바인딩
  const rootProjectLicense = analysisResult?.root_project_license;
  const mainLicense = rootProjectLicense || analysisResult?.ai_guessed_main_license || (isZip ? '다중 라이선스' : 'Unknown');

  // 2) OSADL 충돌 매트릭스 결과 연동
  const conflictSummary = analysisResult?.conflict_summary || {};
  const conflictLevel = conflictSummary.level || 'OK'; // 'CONFLICT' | 'RISK' | 'OK'
  const conflictDetails = conflictSummary.conflicts || [];

  const getRealAiReasoning = () => {
    if (!analysisResult) return 'AI 분석 결과가 존재하지 않습니다.';

    if (!isZip) {
      return analysisResult.ai_analysis_reason || 'AI 분석 결과가 존재하지 않습니다.';
    }

    const results = analysisResult.results || [];
    if (results.length === 0) return 'AI 분석 결과가 존재하지 않습니다.';

    const licenseFile = results.find(r => {
      const name = (r.filename || r.file_path || '').toUpperCase();
      return name.includes('LICENSE') || name.includes('COPYING');
    });

    const codeFiles = results.filter(r => {
      const name = (r.filename || r.file_path || '').toUpperCase();
      return !name.includes('LICENSE') && !name.includes('COPYING');
    });

    const summaries = [];
    if (rootProjectLicense) {
      summaries.push(`🏛️ [2-Pass Root Project License] : 전체 프로젝트 대표 규격으로 '${rootProjectLicense}' 라이선스가 확정되었습니다.`);
    }
    if (licenseFile && licenseFile.ai_analysis_reason) {
      summaries.push(`📄 [${licenseFile.filename || licenseFile.file_path} Root License Text] : ${licenseFile.ai_analysis_reason}`);
    }
    if (codeFiles.length > 0 && codeFiles[0].ai_analysis_reason) {
      summaries.push(`💻 [${codeFiles[0].filename || codeFiles[0].file_path} Source Code] : ${codeFiles[0].ai_analysis_reason}`);
    }

    return summaries.length > 0 
      ? summaries.join('\n\n') 
      : results.slice(0, 2).map(r => `• ${r.filename || r.file_path}: ${r.ai_analysis_reason}`).join('\n\n');
  };

  const aiReason = getRealAiReasoning();
  
  // 3) 가변 컴플라이언스 및 OSADL 충돌 기반 정밀 위험도 계산
  let riskPercent = 10;
  let riskStatusText = '호환성 안전 (Compliant)';
  let isDanger = false;
  let isWarning = false;

  if (conflictLevel === 'CONFLICT') {
    riskPercent = 96;
    riskStatusText = 'OSADL 법적 비호환 라이선스 충돌 (Incompatible Conflict)';
    isDanger = true;
  } else if (conflictLevel === 'RISK') {
    riskPercent = 68;
    riskStatusText = 'OSADL 라이선스 결합 주의 및 잠재적 리스크 (Conditional Risk)';
    isWarning = true;
  } else if (isZip) {
    const dist = analysisResult?.summary?.license_distribution || {};
    const hasCopyleft = Object.keys(dist).some(k => {
      const u = k.toUpperCase();
      return u.includes('GPL') || u.includes('AGPL');
    });

    if (hasCopyleft && projectTarget === 'commercial') {
      riskPercent = 90;
      riskStatusText = '카피레프트(GPL 계열) 상용 배포 정책 위반 (Policy Violation)';
      isDanger = true;
    } else {
      riskPercent = 15;
      riskStatusText = '전체 프로젝트 호환성 양호 (Compliant)';
    }
  } else {
    const uMain = mainLicense.toUpperCase();
    if ((uMain.includes('GPL') || uMain.includes('AGPL')) && projectTarget === 'commercial') {
      riskPercent = 94;
      riskStatusText = `${mainLicense} 바이럴 조항 상용 배포 충돌 (Policy Violation)`;
      isDanger = true;
    } else if (mainLicense === 'Unknown') {
      riskPercent = 55;
      riskStatusText = '라이선스 미명시 (Unknown / Review Required)';
      isWarning = true;
    } else {
      riskPercent = 5;
      riskStatusText = `${mainLicense} 표준 라이선스 (Compliant)`;
    }
  }

  const riskData = [
    { name: '위험', value: riskPercent },
    { name: '안전', value: 100 - riskPercent }
  ];

  // 4) 동적 테이블 데이터 구성 (상속 여부 및 유효 라이선스 매핑)
  let dynamicTableData = [];
  if (isZip && analysisResult?.results) {
    dynamicTableData = analysisResult.results.map((res, idx) => {
      const isInherited = Boolean(res.is_inherited);
      const effectiveLicense = (isInherited && res.inherited_license) 
        ? res.inherited_license 
        : (res.ai_guessed_main_license || 'Unknown');
      
      const uLic = effectiveLicense.toUpperCase();
      let tier = 'Safe';
      if (uLic.includes('GPL') || uLic.includes('AGPL')) {
        tier = 'Critical';
      } else if (uLic.includes('LGPL') || uLic.includes('MPL') || effectiveLicense === 'Unknown') {
        tier = 'Medium';
      }

      return {
        key: String(idx),
        fileName: res.file_path || res.filename,
        location: res.language || 'Unknown',
        issueType: effectiveLicense,
        isInherited: isInherited,
        origAiGuessed: res.ai_guessed_main_license || 'Unknown',
        riskLevel: tier,
        codeSnippet: `${Object.keys(res.detected_packages || {}).length}개 의존성 감지`,
        description: res.ai_analysis_reason,
        packages: Object.keys(res.detected_packages || {})
      };
    });
  } else if (analysisResult) {
    const pkgs = Object.keys(analysisResult.detected_packages || {});
    dynamicTableData.push({
      key: '1',
      fileName: analysisResult.filename || uploadedFile?.name || 'source_code',
      location: analysisResult.language || '소스 헤더',
      issueType: mainLicense,
      isInherited: false,
      origAiGuessed: mainLicense,
      riskLevel: isDanger ? 'Critical' : isWarning ? 'Medium' : 'Safe',
      codeSnippet: pkgs.length > 0 ? pkgs.slice(0, 3).join(', ') : '단독 스크립트',
      description: aiReason,
      packages: pkgs
    });
  }

  // -------------------------------------------------------------
  // [리포트 다운로드 핸들러]
  // -------------------------------------------------------------
  const handleExportCSV = () => {
    if (!analysisResult) {
      message.warning('내보낼 분석 결과가 없습니다.');
      return;
    }

    const rows = [
      ['파일명/경로', '언어', '최종 유효 라이선스', '상속 여부', '원본 AI 판별', '위험 수준', '의존성 정보', 'AI 분석 근거(Reasoning)']
    ];

    dynamicTableData.forEach(item => {
      rows.push([
        `"${item.fileName}"`,
        `"${item.location}"`,
        `"${item.issueType}"`,
        `"${item.isInherited ? '상속됨(Inherited)' : '개별 식별'}"`,
        `"${item.origAiGuessed}"`,
        `"${item.riskLevel}"`,
        `"${item.codeSnippet}"`,
        `"${(item.description || '').replace(/"/g, '""')}"`
      ]);
    });

    const csvContent = '\uFEFF' + rows.map(e => e.join(',')).join('\n');
    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    const downloadName = (analysisResult.filename || uploadedFile?.name || 'project').replace(/\.[^/.]+$/, "");
    link.setAttribute('download', `SBOM_Report_${downloadName}.csv`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    message.success('SBOM CSV 리포트가 성공적으로 다운로드되었습니다.');
  };

  const handleExportSBOMJson = () => {
    if (!analysisResult) {
      message.warning('내보낼 분석 결과가 없습니다.');
      return;
    }

    const projectName = (analysisResult.filename || uploadedFile?.name || 'project').replace(/\.[^/.]+$/, "");
    const sbomData = {
      spdxVersion: "SPDX-2.3",
      dataLicense: "CC0-1.0",
      SPDXID: "SPDXRef-DOCUMENT",
      name: projectName,
      documentNamespace: `http://spdx.org/spdxdocs/${projectName}-${Date.now()}`,
      creationInfo: {
        creators: ["Tool: SCA-Security-Guard-AI", `Person: ${currentUser}`],
        created: new Date().toISOString()
      },
      projectSummary: {
        riskScore: `${riskPercent}%`,
        complianceStatus: riskStatusText,
        rootProjectLicense: rootProjectLicense || mainLicense,
        conflictLevel: conflictLevel,
        conflictDetails: conflictDetails,
        deploymentMode: projectTarget,
        totalFiles: isZip ? (analysisResult.summary?.total_files || 0) : 1,
        licenseDistribution: isZip ? analysisResult.summary?.license_distribution : { [mainLicense]: 1 }
      },
      packages: dynamicTableData.map((item, idx) => ({
        SPDXID: `SPDXRef-Package-${idx + 1}`,
        name: item.fileName,
        versionInfo: "1.0.0",
        licenseConcluded: item.issueType,
        licenseDeclared: item.issueType,
        isInherited: item.isInherited,
        originalDetection: item.origAiGuessed,
        riskLevel: item.riskLevel,
        dependencies: item.codeSnippet,
        comment: item.description
      }))
    };

    const blob = new Blob([JSON.stringify(sbomData, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.setAttribute('download', `SBOM_${projectName}.json`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    message.success('SPDX 2.3 규격 SBOM JSON 파일이 성공적으로 다운로드되었습니다.');
  };

  const handleExportPDF = () => {
    if (!analysisResult) {
      message.warning('내보낼 분석 결과가 없습니다.');
      return;
    }
    window.print();
  };

  // ==========================================
  // [화면 1] 로그인 화면
  // ==========================================
  if (currentStep === 'login') {
    return (
      <div style={{
        minHeight: '100vh',
        display: 'flex',
        justifyContent: 'center',
        alignItems: 'center',
        background: 'linear-gradient(135deg, #0f172a 0%, #1e293b 100%)'
      }}>
        <Card style={{ width: 430, borderRadius: 12, boxShadow: '0 8px 24px rgba(0,0,0,0.3)' }}>
          <div style={{ textAlign: 'center', marginBottom: 26 }}>
            <SafetyCertificateOutlined style={{ fontSize: 44, color: '#1890ff', marginBottom: 12 }} />
            <Title level={3} style={{ margin: 0 }}>SCA Security Guard</Title>
            <Text type="secondary">다국어 오픈소스 라이선스 & 보안 분석 플랫폼</Text>
          </div>

          <Form layout="vertical" onFinish={handleLogin} initialValues={{ email: currentUser }}>
            <Form.Item label="아이디 / 이메일" name="email" rules={[{ required: true, message: '아이디를 입력해주세요!' }]}>
              <Input prefix={<UserOutlined />} placeholder="아이디 또는 이메일" size="large" />
            </Form.Item>

            <Form.Item label="비밀번호" name="password" rules={[{ required: true, message: '비밀번호를 입력해주세요!' }]}>
              <Input.Password prefix={<LockOutlined />} placeholder="••••••••" size="large" />
            </Form.Item>

            <Form.Item style={{ marginTop: 24, marginBottom: 10 }}>
              <Button type="primary" htmlType="submit" size="large" block style={{ height: 46, fontWeight: 'bold' }}>
                로그인 및 시스템 접속
              </Button>
            </Form.Item>

            <Divider style={{ margin: '14px 0', fontSize: 13, color: '#8c8c8c' }}>계정이 없으신가요?</Divider>

            <Button 
              type="default" 
              icon={<UserAddOutlined />} 
              size="large" 
              block 
              style={{ height: 44, borderColor: '#52c41a', color: '#52c41a', fontWeight: 'bold' }}
              onClick={() => setCurrentStep('signup')}
            >
              신규 회원가입
            </Button>
          </Form>
        </Card>
      </div>
    );
  }

  // ==========================================
  // [화면 1-1] 회원가입 창
  // ==========================================
  if (currentStep === 'signup') {
    return (
      <div style={{
        minHeight: '100vh',
        display: 'flex',
        justifyContent: 'center',
        alignItems: 'center',
        background: 'linear-gradient(135deg, #0f172a 0%, #1e293b 100%)'
      }}>
        <Card style={{ width: 440, borderRadius: 12, boxShadow: '0 8px 24px rgba(0,0,0,0.3)' }}>
          <div style={{ textAlign: 'center', marginBottom: 24 }}>
            <UserAddOutlined style={{ fontSize: 40, color: '#52c41a', marginBottom: 10 }} />
            <Title level={3} style={{ margin: 0 }}>신규 계정 회원가입</Title>
            <Text type="secondary">SCA 보안 분석 시스템의 계정을 생성합니다.</Text>
          </div>

          <Form layout="vertical" onFinish={handleSignup}>
            <Form.Item 
              label="아이디" 
              name="userId" 
              rules={[{ required: true, message: '사용할 아이디를 입력해주세요!' }]}
            >
              <Input prefix={<UserOutlined />} placeholder="아이디 입력 (예: user01)" size="large" />
            </Form.Item>

            <Form.Item 
              label="비밀번호" 
              name="password" 
              rules={[{ required: true, message: '비밀번호를 입력해주세요!' }]}
              hasFeedback
            >
              <Input.Password prefix={<LockOutlined />} placeholder="비밀번호 입력" size="large" />
            </Form.Item>

            <Form.Item 
              label="비밀번호 확인" 
              name="confirmPassword" 
              dependencies={['password']}
              hasFeedback
              rules={[
                { required: true, message: '비밀번호 확인을 입력해주세요!' },
                ({ getFieldValue }) => ({
                  validator(_, value) {
                    if (!value || getFieldValue('password') === value) {
                      return Promise.resolve();
                    }
                    return Promise.reject(new Error('비밀번호와 비밀번호 확인이 일치하지 않습니다!'));
                  },
                }),
              ]}
            >
              <Input.Password prefix={<LockOutlined />} placeholder="비밀번호 재입력" size="large" />
            </Form.Item>

            <Form.Item style={{ marginTop: 24, marginBottom: 10 }}>
              <Button type="primary" htmlType="submit" size="large" block style={{ height: 46, fontWeight: 'bold', background: '#52c41a' }}>
                회원가입 완료
              </Button>
            </Form.Item>

            <Button 
              type="link" 
              block 
              icon={<ArrowLeftOutlined />}
              onClick={() => setCurrentStep('login')}
            >
              로그인 화면으로 돌아가기
            </Button>
          </Form>
        </Card>
      </div>
    );
  }

  // ==========================================
  // [화면 2] 업로드 화면
  // ==========================================
  if (currentStep === 'upload') {
    return (
      <div style={{ minHeight: '100vh', background: '#f8fafc', padding: '40px 20px' }}>
        <div style={{ maxWidth: 880, margin: '0 auto' }}>
          <Card style={{ borderRadius: 12, boxShadow: '0 4px 16px rgba(0,0,0,0.06)' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <div>
                <Title level={3} style={{ margin: 0 }}>📁 소스코드 및 프로젝트 검증 등록</Title>
                <Text type="secondary">접속 계정: <b>{currentUser}</b></Text>
              </div>
              <Button danger icon={<LogoutOutlined />} onClick={handleLogout}>
                로그아웃
              </Button>
            </div>
            <Paragraph type="secondary" style={{ marginTop: 12 }}>
              다국어 소스코드(Python, Java, JS, C) 또는 전체 프로젝트가 포함된 <b>.zip 파일</b>을 업로드하세요.
            </Paragraph>
            <Divider />

            <div style={{ marginBottom: 24 }}>
              <Text strong style={{ display: 'block', marginBottom: 8 }}>
                1. 프로젝트 배포 목적 선택 (가변 컴플라이언스 평가):
              </Text>
              <Select 
                value={projectTarget} 
                onChange={setProjectTarget} 
                size="large" 
                style={{ width: '100%' }}
              >
                <Option value="commercial">상용 소프트웨어 배포 (GPL 독소 조항 엄격 판정 - 최고 위험도)</Option>
                <Option value="internal">사내/연구 내부망 시스템 (독립 실행 허용 - 중간 위험도)</Option>
                <Option value="opensource">오픈소스 공개 프로젝트 (자유 결합 허용 - 최소 위험도)</Option>
              </Select>
            </div>

            <div style={{ marginBottom: 24 }}>
              <Text strong style={{ display: 'block', marginBottom: 8 }}>2. 소스코드 및 프로젝트 파일 업로드:</Text>
              <Dragger 
                multiple={false} 
                accept=".py,.js,.jsx,.java,.c,.cpp,.h,.zip,.xml,.json,.txt"
                beforeUpload={(file) => {
                  setUploadedFile(file);
                  if (file.name.toLowerCase().endsWith('.zip')) {
                    setFileContent('// 📦 ZIP 프로젝트 아카이브가 선택되었습니다.\n// 분석 시작 시 서버에서 압축을 해제하고\n// 내부 소스코드(Python, Java, JS, C)를 일괄 순회 분석하며 2-Pass 상속 엔진이 구동됩니다.');
                  } else {
                    const reader = new FileReader();
                    reader.onload = (e) => setFileContent(e.target.result);
                    reader.readAsText(file);
                  }
                  message.info(`파일 선택됨: ${file.name} (${(file.size / 1024).toFixed(1)} KB)`);
                  return false;
                }}
                onRemove={() => {
                  setUploadedFile(null);
                  setFileContent('');
                  setSelectedRowFile(null);
                }}
                style={{ padding: '24px 0', background: '#fafafa', borderRadius: 8 }}
              >
                <p className="ant-upload-drag-icon">
                  <InboxOutlined style={{ color: '#1890ff', fontSize: 48 }} />
                </p>
                <p className="ant-upload-text" style={{ fontWeight: 'bold' }}>클릭하거나 소스코드/ZIP 파일을 이 영역으로 드래그하세요</p>
                <p className="ant-upload-hint">지원 포맷: .zip (다국어 일괄 분석), .py, .java, .js, .c, pom.xml, package.json</p>
              </Dragger>
            </div>

            <div style={{ marginBottom: 32 }}>
              <Text strong style={{ display: 'block', marginBottom: 8 }}>3. 또는 GitHub 저장소 주소 입력:</Text>
              <Input 
                size="large" 
                prefix={<GithubOutlined />} 
                placeholder="https://github.com/organization/target-repository.git" 
                value={repoUrl} 
                onChange={e => setRepoUrl(e.target.value)} 
              />
            </div>

            <Button 
              type="primary" 
              size="large" 
              block 
              onClick={handleStartAnalysis}
              style={{ height: 48, fontSize: 16, fontWeight: 'bold', background: '#1677ff' }}
            >
              🚀 소스코드 분석 및 위험도 진단 시작
            </Button>
          </Card>
        </div>

        {/* 정밀 분석 진행 모달 */}
        <Modal 
          title={<span><SafetyCertificateOutlined style={{ color: '#1890ff' }} /> 오픈소스 보안 및 라이선스 분석 진행 중</span>} 
          open={analyzingModal} 
          footer={null} 
          closable={false}
          centered
          width={520}
        >
          <div style={{ textAlign: 'center', padding: '16px 10px' }}>
            <Progress 
              type="circle" 
              percent={analysisProgress} 
              status={analysisProgress === 100 ? "success" : "active"} 
              strokeWidth={8}
            />
            
            <div style={{ 
              marginTop: 22, 
              background: '#f8fafc', 
              padding: '16px 20px', 
              borderRadius: 8, 
              border: '1px solid #e2e8f0',
              textAlign: 'left'
            }}>
              <Row gutter={16} align="middle">
                <Col span={12}>
                  <div style={{ fontSize: 12, color: '#64748b', marginBottom: 4 }}>총 예상 소요 시간</div>
                  <Tag 
                    color="blue" 
                    icon={<ClockCircleOutlined />} 
                    style={{ fontSize: 13, padding: '4px 10px', whiteSpace: 'nowrap', display: 'inline-block' }}
                  >
                    {`약 ${totalEstimatedSec}초`}
                  </Tag>
                </Col>

                <Col span={12}>
                  <div style={{ fontSize: 12, color: '#64748b', marginBottom: 4 }}>남은 예상 시간</div>
                  <Tag 
                    color={remainingSec <= 2 ? "green" : "volcano"} 
                    icon={<SyncOutlined spin={analysisProgress < 100} />} 
                    style={{ fontSize: 13, padding: '4px 10px', whiteSpace: 'nowrap', display: 'inline-block' }}
                  >
                    {`약 ${remainingSec}초 남음`}
                  </Tag>
                </Col>
              </Row>

              <Divider style={{ margin: '12px 0' }} />
              
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, color: '#475569' }}>
                <span>실제 경과: <b>{elapsedSec}초</b></span>
                <span>진행: <b>{totalEstimatedSec}초 중 {Math.min(totalEstimatedSec, totalEstimatedSec - remainingSec)}초 완료</b></span>
              </div>
            </div>

            <div style={{ marginTop: 18 }}>
              <Text strong style={{ fontSize: 14, color: '#1890ff' }}>
                {analysisStatusText}
              </Text>
            </div>
            
            <Paragraph type="secondary" style={{ marginTop: 8, fontSize: 12, minHeight: 18 }}>
              {analysisSubText}
            </Paragraph>
          </div>
        </Modal>
      </div>
    );
  }

  // ==========================================
  // [화면 4] 메인 대시보드 및 상용 SCA 엔지니어링 리포트
  // ==========================================
  const currentDateStr = new Date().toISOString().replace('T', ' ').substring(0, 19) + ' KST';
  const docNumber = `SCA-AUDIT-${new Date().getFullYear()}-${String(analysisResult?.summary?.total_files || 1).padStart(4, '0')}`;
  const projectNameDisplay = analysisResult?.filename || uploadedFile?.name || 'Target-Artifact';

  const totalFilesCount = isZip ? (analysisResult?.summary?.total_files || 0) : 1;
  const inheritedFilesCount = dynamicTableData.filter(d => d.isInherited).length;
  const unknownCount = dynamicTableData.filter(d => d.issueType === 'Unknown').length;
  const copyleftCount = dynamicTableData.filter(d => d.issueType.toUpperCase().includes('GPL') || d.issueType.toUpperCase().includes('AGPL')).length;
  const permissiveCount = dynamicTableData.length - unknownCount - copyleftCount;

  return (
    <div style={{ minHeight: '100vh', background: '#f5f7fa' }}>
      
      {/* 
        ============================================================
        [인쇄 전용 테크니컬 리포트 스타일 시트 및 행 선택 하이라이트]
        ============================================================
      */}
      <style>{`
        .ant-table-row-selected > td {
          background-color: #e6f4ff !important;
        }

        .print-only-report {
          display: none;
        }

        @media print {
          @page {
            size: A4 portrait;
            margin: 12mm 14mm 12mm 14mm;
          }

          body, html {
            background: #ffffff !important;
            color: #0f172a !important;
            margin: 0 !important;
            padding: 0 !important;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", "Malgun Gothic", monospace !important;
            -webkit-print-color-adjust: exact !important;
            print-color-adjust: exact !important;
          }

          .screen-only {
            display: none !important;
          }

          .print-only-report {
            display: block !important;
            width: 100% !important;
          }

          .sca-header {
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            border-bottom: 2px solid #0f172a;
            padding-bottom: 12px;
            margin-bottom: 16px;
          }

          .sca-brand-tag {
            font-size: 10px;
            font-family: monospace;
            font-weight: 700;
            letter-spacing: 1.5px;
            color: #475569;
            text-transform: uppercase;
          }

          .sca-title {
            font-size: 19px;
            font-weight: 800;
            color: #0f172a;
            margin: 3px 0 4px 0;
            letter-spacing: -0.3px;
          }

          .sca-verdict-box {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 6px 14px;
            border-radius: 4px;
            font-size: 12px;
            font-weight: 800;
            font-family: monospace;
            letter-spacing: 0.5px;
            text-transform: uppercase;
          }
          .verdict-pass {
            background-color: #ecfdf5 !important;
            border: 1px solid #059669 !important;
            color: #065f46 !important;
          }
          .verdict-fail {
            background-color: #fff1f2 !important;
            border: 1px solid #e11d48 !important;
            color: #9f1239 !important;
          }
          .verdict-warn {
            background-color: #fffbeb !important;
            border: 1px solid #d97706 !important;
            color: #b45309 !important;
          }

          .meta-grid {
            width: 100%;
            border-collapse: collapse;
            font-size: 11px;
            margin-bottom: 16px;
            background: #f8fafc;
            border: 1px solid #e2e8f0;
          }
          .meta-grid td {
            padding: 6px 10px;
            border: 1px solid #e2e8f0;
          }
          .meta-label {
            font-weight: 700;
            color: #475569;
            width: 18%;
            background: #f1f5f9;
          }
          .meta-value {
            font-family: monospace;
            color: #0f172a;
            width: 32%;
          }

          .metrics-row {
            display: flex;
            gap: 10px;
            margin-bottom: 18px;
          }
          .metric-card {
            flex: 1;
            border: 1px solid #cbd5e1;
            padding: 8px 12px;
            background: #ffffff;
            border-radius: 4px;
          }
          .metric-title {
            font-size: 10px;
            font-weight: 700;
            color: #64748b;
            text-transform: uppercase;
            letter-spacing: 0.5px;
          }
          .metric-val {
            font-size: 20px;
            font-weight: 800;
            color: #0f172a;
            font-family: monospace;
            margin-top: 2px;
          }

          .section-heading {
            font-size: 12px;
            font-weight: 800;
            text-transform: uppercase;
            letter-spacing: 0.8px;
            color: #0f172a;
            border-left: 3px solid #2563eb;
            padding-left: 8px;
            margin: 16px 0 8px 0;
            display: flex;
            align-items: center;
            justify-content: space-between;
          }

          .remediation-box {
            border: 1px solid #e2e8f0;
            background: #fafafa;
            border-radius: 4px;
            padding: 10px 14px;
            font-size: 11px;
            line-height: 1.55;
            margin-bottom: 16px;
          }

          .sbom-table {
            width: 100%;
            border-collapse: collapse;
            font-size: 10px;
            margin-top: 8px;
            page-break-inside: auto;
          }
          .sbom-table thead tr {
            background-color: #0f172a !important;
            color: #ffffff !important;
          }
          .sbom-table th {
            padding: 6px 8px;
            font-weight: 700;
            text-align: left;
            border: 1px solid #0f172a;
            letter-spacing: 0.3px;
          }
          .sbom-table td {
            padding: 5px 8px;
            border: 1px solid #cbd5e1;
            vertical-align: top;
          }
          .sbom-table tr:nth-child(even) {
            background-color: #f8fafc;
          }
          .sbom-path {
            font-family: monospace;
            font-weight: 600;
            color: #0f172a;
          }
          .sbom-badge {
            display: inline-block;
            padding: 1px 5px;
            font-size: 9px;
            font-weight: 700;
            font-family: monospace;
            border-radius: 2px;
            border: 1px solid transparent;
          }
          .badge-safe {
            background: #ecfdf5;
            color: #047857;
            border-color: #a7f3d0;
          }
          .badge-warn {
            background: #fffbeb;
            color: #b45309;
            border-color: #fde68a;
          }
          .badge-crit {
            background: #fef2f2;
            color: #b91c1c;
            border-color: #fecaca;
          }

          .sbom-reason {
            font-size: 9.5px;
            color: #334155;
            line-height: 1.35;
          }

          .sca-footer {
            margin-top: 20px;
            border-top: 1px solid #e2e8f0;
            padding-top: 8px;
            display: flex;
            justify-content: space-between;
            font-size: 9px;
            color: #64748b;
            font-family: monospace;
          }
        }
      `}</style>

      {/* ========================================================= */}
      {/* 📄 [1] 엔지니어링 표준 인쇄 리포트 (화면에서는 숨김)         */}
      {/* ========================================================= */}
      <div className="print-only-report">
        <div className="sca-header">
          <div>
            <div className="sca-brand-tag">SCA SECURITY GUARD // COMPLIANCE & SBOM AUDIT</div>
            <div className="sca-title">Software License & Composition Report</div>
            <div style={{ fontSize: 11, color: '#475569', marginTop: 2 }}>
              Automated Static Tree-sitter & 2-Pass Inheritance Engine Audit Trail (OSADL Matrix Verified)
            </div>
          </div>
          <div style={{ textAlign: 'right' }}>
            <div className={`sca-verdict-box ${isDanger ? 'verdict-fail' : isWarning ? 'verdict-warn' : 'verdict-pass'}`}>
              {isDanger ? <CloseCircleOutlined /> : isWarning ? <ExclamationCircleOutlined /> : <CheckOutlined />}
              <span>{isDanger ? 'POLICY VIOLATION' : isWarning ? 'CONDITIONAL RISK' : 'POLICY COMPLIANT'}</span>
            </div>
            <div style={{ fontSize: 10, color: '#64748b', marginTop: 4, fontFamily: 'monospace' }}>
              DOC ID: {docNumber}
            </div>
          </div>
        </div>

        <table className="meta-grid">
          <tbody>
            <tr>
              <td className="meta-label">Target Artifact</td>
              <td className="meta-value"><b>{projectNameDisplay}</b></td>
              <td className="meta-label">Scan Timestamp</td>
              <td className="meta-value">{currentDateStr}</td>
            </tr>
            <tr>
              <td className="meta-label">Policy Rule Profile</td>
              <td className="meta-value">
                {projectTarget === 'commercial' ? 'Commercial Mode (GPL Strict Block)' : projectTarget === 'internal' ? 'Internal Network (Permissive)' : 'Open Source Public'}
              </td>
              <td className="meta-label">Auditor / Operator</td>
              <td className="meta-value">{currentUser}</td>
            </tr>
            <tr>
              <td className="meta-label">Conflict Matrix</td>
              <td className="meta-value">OSADL Compatibility Matrix (Level: <b>{conflictLevel}</b>)</td>
              <td className="meta-label">Inference Engine</td>
              <td className="meta-value">2-Pass Tree-sitter AST & Local Ollama Qwen 2.5</td>
            </tr>
          </tbody>
        </table>

        <div className="metrics-row">
          <div className="metric-card">
            <div className="metric-title">Total Files</div>
            <div className="metric-val">{totalFilesCount}</div>
          </div>
          <div className="metric-card">
            <div className="metric-title">Root Project License</div>
            <div className="metric-val" style={{ fontSize: 16 }}>{mainLicense}</div>
          </div>
          <div className="metric-card">
            <div className="metric-title">Inherited Files</div>
            <div className="metric-val" style={{ color: '#722ed1' }}>{inheritedFilesCount}</div>
          </div>
          <div className="metric-card">
            <div className="metric-title">OSADL Status</div>
            <div className="metric-val" style={{ color: isDanger ? '#dc2626' : isWarning ? '#d97706' : '#059669', fontSize: 16 }}>
              {conflictLevel}
            </div>
          </div>
          <div className="metric-card">
            <div className="metric-title">Copyleft Files</div>
            <div className="metric-val" style={{ color: copyleftCount > 0 ? '#dc2626' : '#059669' }}>{copyleftCount}</div>
          </div>
        </div>

        <div className="section-heading">
          <span>1. AI Contextual Inference & 2-Pass Inheritance Log</span>
          <span style={{ fontSize: 9.5, color: '#64748b' }}>ENGINE VERIFICATION LOG</span>
        </div>
        <div className="remediation-box" style={{ fontFamily: 'monospace', whiteSpace: 'pre-wrap', background: '#f8fafc' }}>
          {aiReason}
        </div>

        <div className="section-heading">
          <span>2. Actionable Remediation & OSADL Guidance</span>
          <span style={{ fontSize: 9.5, color: '#64748b' }}>ENGINEERING CHECKLIST</span>
        </div>
        <div className="remediation-box">
          {isDanger ? (
            <div>
              <div style={{ color: '#b91c1c', fontWeight: 'bold', marginBottom: 4 }}>
                🚨 [Critical Action] License Conflict or Viral Copyleft Hazard Detected
              </div>
              <div>- <b>OSADL 비호환 충돌 격리:</b> 프로젝트 내 라이선스 또는 의존성 간 양립 불가(`CONFLICT`)가 감지되었습니다. 상용 소프트웨어와 결합 시 법적 분쟁 및 소스 공개 의무가 발생합니다.</div>
              <div>- <b>아키텍처 분리:</b> 충돌 라이브러리를 Permissive 라이선스(MIT, Apache-2.0)로 교체하거나 프로세스 분리(gRPC, REST) 구조로 전환하십시오.</div>
            </div>
          ) : isWarning ? (
            <div>
              <div style={{ color: '#b45309', fontWeight: 'bold', marginBottom: 4 }}>
                ⚠️ [Warning Action] Conditional Risk or Unknown Licenses Present
              </div>
              <div>- <b>조건부 호환성 검토:</b> 약한 카피레프트(LGPL/MPL) 또는 미명시(Unknown) 파일이 존재합니다. 링크 방식(동적/정적) 및 라이선스 고지 의무를 재검토하십시오.</div>
            </div>
          ) : (
            <div>
              <div style={{ color: '#047857', fontWeight: 'bold', marginBottom: 4 }}>
                ✅ [Compliant] No Critical License Incompatibilities Found
              </div>
              <div>- <b>고지문 동봉 의무 준수:</b> 식별된 라이선스 규격에 따라 바이너리 배포 산출물에 저작권 공지 및 고지문(<code>THIRD-PARTY-NOTICES.txt</code>)을 동봉하십시오.</div>
            </div>
          )}
        </div>

        <div className="section-heading">
          <span>3. Software Bill of Materials (SBOM) & Component Inventory</span>
          <span style={{ fontSize: 9.5, color: '#64748b' }}>SPDX-COMPLIANT LIST</span>
        </div>
        <table className="sbom-table">
          <thead>
            <tr>
              <th style={{ width: '26%' }}>Source File / Artifact</th>
              <th style={{ width: '10%' }}>Language</th>
              <th style={{ width: '15%' }}>Effective License</th>
              <th style={{ width: '8%' }}>Inherited</th>
              <th style={{ width: '9%' }}>Risk Tier</th>
              <th style={{ width: '14%' }}>Dependencies</th>
              <th>Audit Reasoning & Evidence</th>
            </tr>
          </thead>
          <tbody>
            {dynamicTableData.map((item, idx) => (
              <tr key={idx}>
                <td className="sbom-path">{item.fileName}</td>
                <td>{item.location}</td>
                <td><b>{item.issueType}</b></td>
                <td style={{ textAlign: 'center' }}>
                  {item.isInherited ? <span style={{ color: '#722ed1', fontWeight: 'bold' }}>YES</span> : '-'}
                </td>
                <td>
                  <span className={`sbom-badge ${item.riskLevel === 'Critical' ? 'badge-crit' : item.riskLevel === 'Medium' ? 'badge-warn' : 'badge-safe'}`}>
                    {item.riskLevel}
                  </span>
                </td>
                <td>
                  {item.packages && item.packages.length > 0 ? (
                    <code style={{ fontSize: 9, color: '#1d4ed8' }}>{item.packages.slice(0, 4).join(', ')}</code>
                  ) : (
                    <span style={{ color: '#94a3b8' }}>None (Direct)</span>
                  )}
                </td>
                <td className="sbom-reason">{item.description}</td>
              </tr>
            ))}
          </tbody>
        </table>

        <div className="sca-footer">
          <div>SCA Security Guard v2.0 // 2-Pass Inheritance & OSADL Matrix Engine</div>
          <div>Page 1 of 1 (Consolidated Developer Audit)</div>
        </div>
      </div>

      {/* ========================================================= */}
      {/* 🖥️ [2] 웹 화면 전용 인터랙티브 대시보드 (.screen-only)       */}
      {/* ========================================================= */}
      <div className="screen-only" style={{ padding: '24px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '20px' }}>
          <Space>
            <Button 
              icon={<ArrowLeftOutlined />} 
              onClick={() => {
                setSelectedRowFile(null);
                setCurrentStep('upload');
              }}
            >
              다른 프로젝트 검증
            </Button>
            <Title level={3} style={{ margin: 0 }}>🛡️ AI 오픈소스 컴플라이언스 리포트</Title>
          </Space>
          
          <Space>
            <Tag color="geekblue" style={{ fontSize: '13px', padding: '5px 10px' }}>
              접속자: <b>{currentUser}</b>
            </Tag>
            <Tag color="geekblue" style={{ fontSize: '13px', padding: '5px 10px' }}>
              배포 환경: <b>{projectTarget === 'commercial' ? '상용 배포 모드' : projectTarget === 'internal' ? '사내망 모드' : '오픈소스 모드'}</b>
            </Tag>
            <Tag color={isDanger ? "red" : isWarning ? "gold" : "green"} style={{ fontSize: '14px', padding: '5px 12px' }}>
              {isDanger ? `위험 등급 (${riskPercent}%)` : isWarning ? `주의 등급 (${riskPercent}%)` : `안전 등급 (${riskPercent}%)`}
            </Tag>
            
            <Button danger icon={<LogoutOutlined />} onClick={handleLogout}>
              로그아웃
            </Button>
          </Space>
        </div>

        {/* OSADL 충돌 발생 시 최상단 동적 경고 배너 */}
        {conflictLevel !== 'OK' && (
          <Alert
            message={
              <Space>
                <WarningOutlined style={{ fontSize: 18, color: conflictLevel === 'CONFLICT' ? '#ff4d4f' : '#faad14' }} />
                <span style={{ fontWeight: 'bold', fontSize: 15 }}>
                  {conflictLevel === 'CONFLICT' 
                    ? 'OSADL 법적 비호환 라이선스 충돌 (Incompatible Conflict Detected)' 
                    : 'OSADL 라이선스 결합 주의 (Conditional License Risk)'}
                </span>
              </Space>
            }
            description={
              <div style={{ marginTop: 6, fontSize: 13 }}>
                <div>오픈소스 라이선스 상호 결합 및 OSADL 호환성 매트릭스 대조 결과 비호환성이 확인되었습니다.</div>
                {conflictDetails.length > 0 && (
                  <div style={{ marginTop: 6 }}>
                    <b>감지된 충돌 조합:</b>
                    <ul style={{ margin: '4px 0 0 16px', padding: 0 }}>
                      {conflictDetails.map((c, i) => (
                        <li key={i}>
                          <code>{c.license1 || c.target}</code> ⚡ <code>{c.license2 || c.dep}</code> : {c.reason || '상호 양립 불가 조항'}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            }
            type={conflictLevel === 'CONFLICT' ? "error" : "warning"}
            showIcon={false}
            style={{ marginBottom: 18, borderRadius: 8 }}
          />
        )}

        <Row gutter={[16, 16]} style={{ marginBottom: '16px' }}>
          <Col span={8}>
            <Card title="컴플라이언스 리스크" bordered={false} style={{ textAlign: 'center', height: '240px' }}>
              <ResponsiveContainer width="100%" height={120}>
                <PieChart>
                  <Pie data={riskData} innerRadius={50} outerRadius={70} startAngle={180} endAngle={0} dataKey="value">
                    <Cell fill={isDanger ? "#ff4d4f" : isWarning ? "#faad14" : "#52c41a"} />
                    <Cell fill="#d9d9d9" />
                  </Pie>
                </PieChart>
              </ResponsiveContainer>
              <div style={{ marginTop: '-20px' }}>
                <Title level={2} style={{ margin: 0 }}>{riskPercent}%</Title>
                <Text type={isDanger ? "danger" : isWarning ? "warning" : "success"} strong>{riskStatusText}</Text>
              </div>
            </Card>
          </Col>
          
          <Col span={8}>
            <Card title="루트 대표 라이선스 판별 (2-Pass)" bordered={false} style={{ textAlign: 'center', height: '240px' }}>
              <div style={{ marginTop: '20px' }}>
                <Title level={1} style={{ fontSize: '38px', margin: 0 }}>
                  {mainLicense} {isDanger ? <WarningOutlined style={{ color: '#ff4d4f' }} /> : <CheckCircleOutlined style={{ color: '#52c41a' }} />}
                </Title>
                <div style={{ marginTop: 8 }}>
                  <Text strong>분석 대상: </Text>
                  <Tag color="blue">{projectNameDisplay}</Tag>
                  {rootProjectLicense && <Tag color="purple">2-Pass 엔진 확정</Tag>}
                </div>
              </div>
            </Card>
          </Col>

          <Col span={8}>
            <Card title="검증된 의존성 및 상속 현황" bordered={false} style={{ textAlign: 'center', height: '240px' }}>
              <div style={{ marginTop: '16px' }}>
                <Title level={1} style={{ fontSize: '46px', margin: 0 }}>
                  {isZip ? (analysisResult?.summary?.total_files || 0) : Object.keys(analysisResult?.detected_packages || {}).length}
                </Title>
                <Text type="secondary">{isZip ? '총 분석 파일 수' : '감지된 외부 패키지 수'}</Text><br/>
                <div style={{ marginTop: 4 }}>
                  {isZip && <Tag color="purple">상속 구제 파일: {inheritedFilesCount}개</Tag>}
                  <Tag color="cyan">OSADL: {conflictLevel}</Tag>
                </div>
              </div>
            </Card>
          </Col>
        </Row>

        <Card 
          title={<span><FileExclamationOutlined style={{ color: isDanger ? '#ff4d4f' : '#1890ff' }} /> 검증 파일 목록 및 라이선스 판별 결과 (행을 클릭하면 하단 뷰어에 표시됩니다)</span>} 
          bordered={false} 
          style={{ marginBottom: '16px' }}
        >
          <Table 
            dataSource={dynamicTableData} 
            pagination={{ pageSize: 5 }}
            onRow={(record) => ({
              onClick: () => {
                setSelectedRowFile(record);
                message.info(`'${record.fileName}' 파일 상세 정보를 로드했습니다.`);
              },
              style: { cursor: 'pointer' }
            })}
            rowClassName={(record) => selectedRowFile?.fileName === record.fileName ? 'ant-table-row-selected' : ''}
            columns={[
              {
                title: '파일명 / 경로',
                dataIndex: 'fileName',
                key: 'fileName',
                render: text => <Text strong code style={{ fontSize: 13 }}>{text}</Text>
              },
              {
                title: '언어',
                dataIndex: 'location',
                key: 'location',
                width: 100,
                render: loc => <Tag color="blue">{loc}</Tag>
              },
              {
                title: '판별 라이선스',
                dataIndex: 'issueType',
                key: 'issueType',
                render: (type, rec) => (
                  <Space orientation="horizontal" size={4}>
                    <Tag color={type.includes('GPL') ? 'red' : type === 'Unknown' ? 'orange' : 'green'}>
                      {type}
                    </Tag>
                    {rec.isInherited && (
                      <Tag color="purple" style={{ fontSize: 11 }}>
                        상속됨
                      </Tag>
                    )}
                  </Space>
                )
              },
              {
                title: '위험 수준',
                dataIndex: 'riskLevel',
                key: 'riskLevel',
                width: 100,
                render: level => <Tag color={level === 'Critical' ? 'red' : level === 'Medium' ? 'volcano' : 'green'}>{level}</Tag>
              },
              {
                title: '의존성 정보',
                dataIndex: 'codeSnippet',
                key: 'codeSnippet',
                render: snippet => <code style={{ color: '#0958d9', background: '#e6f4ff', padding: '2px 6px', borderRadius: 4 }}>{snippet}</code>
              },
              {
                title: 'AI 분석 근거 (Reasoning)',
                dataIndex: 'description',
                key: 'description',
                ellipsis: true
              }
            ]}
          />
        </Card>

        <Tabs defaultActiveKey="analysis" size="large" type="card">
          <TabPane tab={<span><CodeOutlined /> 소스코드 & Tree-sitter 하이라이트</span>} key="analysis">
            <Row gutter={[16, 16]}>
              <Col span={14}>
                <Card 
                  title={<span><DeploymentUnitOutlined /> 소스코드 뷰어 ({selectedRowFile ? selectedRowFile.fileName : projectNameDisplay})</span>} 
                  bordered={false} 
                  style={{ height: '540px', display: 'flex', flexDirection: 'column' }}
                >
                  <div style={{ textAlign: 'center', marginBottom: '14px', background: '#fafafa', padding: '10px', borderRadius: '8px' }}>
                     <div style={{ marginBottom: '6px' }}>
                       <Tag color="blue">
                         {selectedRowFile ? selectedRowFile.location : (analysisResult?.language || (isZip ? 'ZIP Archive' : 'Source'))}
                       </Tag>
                       <Tag color={
                         selectedRowFile 
                           ? (selectedRowFile.riskLevel === 'Critical' ? 'red' : selectedRowFile.riskLevel === 'Medium' ? 'orange' : 'green')
                           : (isDanger ? 'red' : 'green')
                       }>
                         {selectedRowFile ? selectedRowFile.issueType : mainLicense}
                       </Tag>
                       {selectedRowFile?.isInherited && (
                         <Tag color="purple">2-Pass 프로젝트 루트 상속</Tag>
                       )}
                     </div>
                     <div style={{ 
                       color: (selectedRowFile ? selectedRowFile.riskLevel === 'Critical' : isDanger) ? '#ff4d4f' : '#52c41a', 
                       fontSize: '13px' 
                     }}>
                       {(selectedRowFile ? selectedRowFile.riskLevel === 'Critical' : isDanger) 
                         ? <><WarningOutlined /> <b>라이선스 충돌 위험 감지</b></> 
                         : <><CheckCircleOutlined /> <b>라이선스 규격 검증 완료</b></>}
                     </div>
                  </div>

                  <div style={{ 
                    background: '#001529', 
                    color: '#fff', 
                    padding: '15px', 
                    borderRadius: '5px', 
                    fontFamily: 'monospace', 
                    fontSize: '12px',
                    flex: 1,
                    overflowY: 'auto',
                    whiteSpace: 'pre-wrap',
                    lineHeight: '1.6'
                  }}>
                    {isZip && selectedRowFile ? (
`// ==========================================
// 📄 선택된 파일: ${selectedRowFile.fileName}
// 🏷️ 판별 라이선스: ${selectedRowFile.issueType} ${selectedRowFile.isInherited ? '(🏛️ 2-Pass 상속 엔진 적용)' : '(단독 식별)'}
// 🔍 원본 AI 추론: ${selectedRowFile.origAiGuessed}
// 📦 의존성 현황: ${selectedRowFile.codeSnippet}
// ==========================================

// 💡 AI 심층 분석 의견 (Reasoning):
// ${selectedRowFile.description || '상세 판정 의견이 없습니다.'}

// 📌 Tree-sitter AST 파싱 및 OSADL 호환성 매트릭스 검증 완료`
                    ) : (
                      fileContent || '// 업로드된 소스코드 내용이 없습니다.'
                    )}
                  </div>
                </Card>
              </Col>

              <Col span={10}>
                <Card title={<span><BulbOutlined /> AI 가이드 및 해결 방안</span>} bordered={false} style={{ height: '540px', overflowY: 'auto' }}>
                  <div style={{ background: isDanger ? '#fff1f0' : '#f6ffed', padding: '14px', borderRadius: '8px', border: `1px solid ${isDanger ? '#ffa39e' : '#b7eb8f'}`, marginBottom: '14px' }}>
                    <Title level={5} style={{ marginBottom: 8 }}><InfoCircleOutlined /> AI 분석 요약</Title>
                    <Paragraph style={{ margin: 0, fontSize: 13, whiteSpace: 'pre-wrap' }}>
                      {aiReason}
                    </Paragraph>
                  </div>

                  <div style={{ background: '#f8fafc', padding: '14px', borderRadius: '8px', border: '1px solid #e2e8f0', marginBottom: '14px' }}>
                    <Title level={5} style={{ marginBottom: 8 }}><CheckCircleOutlined style={{ color: '#52c41a' }} /> 조치 권장사항</Title>
                    <List
                      size="small"
                      dataSource={
                        isDanger
                          ? [
                              `${mainLicense} 바이럴 조항에 따라 상용 소프트웨어 배포 시 소스코드 공개 의무가 발생할 수 있습니다.`,
                              'OSADL 호환성 매트릭스에 따라 양립 불가 충돌 라이브러리를 Permissive(MIT, Apache-2.0)로 교체하십시오.',
                              '불가피할 경우 마이크로서비스 또는 독립 프로세스로 아키텍처를 격리하십시오.'
                            ]
                          : isWarning
                          ? [
                              '조건부 카피레프트(LGPL/MPL) 또는 라이선스 미명시 파일에 대한 추가 검토가 필요합니다.',
                              '동적 링크 라이브러리 참조 방식을 점검하고 소스코드 상단 헤더에 SPDX 식별자를 보완하십시오.'
                            ]
                          : [
                              '식별된 라이선스는 현재 프로젝트 배포 환경과 법적 충돌이 없습니다.',
                              '고지문(Notice) 및 저작권자 표기 의무를 준수하여 배포 산출물을 생성하십시오.'
                            ]
                      }
                      renderItem={(item, index) => <List.Item style={{ padding: '4px 0' }}>{index + 1}. {item}</List.Item>}
                    />
                  </div>

                  <Space direction="vertical" style={{ width: '100%' }}>
                    <Button 
                      type="primary" 
                      icon={<FilePdfOutlined />} 
                      block 
                      size="large" 
                      style={{ height: '42px', fontWeight: 'bold', background: '#1677ff' }} 
                      onClick={handleExportPDF}
                    >
                      컴플라이언스 리포트 내보내기 (PDF)
                    </Button>
                    
                    <Row gutter={8}>
                      <Col span={12}>
                        <Button 
                          icon={<FileExcelOutlined />} 
                          block 
                          style={{ height: '38px', fontWeight: 'bold' }} 
                          onClick={handleExportCSV}
                        >
                          SBOM (CSV)
                        </Button>
                      </Col>
                      <Col span={12}>
                        <Button 
                          icon={<FileTextOutlined />} 
                          block 
                          style={{ height: '38px', fontWeight: 'bold' }} 
                          onClick={handleExportSBOMJson}
                        >
                          SPDX (JSON)
                        </Button>
                      </Col>
                    </Row>
                  </Space>
                </Card>
              </Col>
            </Row>
          </TabPane>

          <TabPane tab={<span><BugOutlined /> 보안 취약점 (CVE) 분석</span>} key="cve">
            <Card title="NVD 국가취약점 DB 매칭 결과" bordered={false}>
              <Alert 
                message="의존성 보안 취약점 교차 점검" 
                description="추출된 외부 패키지 버전 정보를 바탕으로 알려진 CVE 보안 결함을 식별합니다." 
                type="info" 
                showIcon 
                style={{ marginBottom: 16 }}
              />
              <div style={{ background: '#fafafa', padding: 16, borderRadius: 8 }}>
                <Text strong>식별된 의존성 패키지 목록:</Text>
                <ul style={{ marginTop: 8, paddingLeft: 20 }}>
                  {Object.entries(analysisResult?.detected_packages || {}).map(([pkg, info]) => (
                    <li key={pkg}>
                      <code>{pkg}</code> → 라이선스: <b>{info.license}</b> (저장소: {info.registry || 'Unknown'})
                    </li>
                  ))}
                  {Object.keys(analysisResult?.detected_packages || {}).length === 0 && (
                    <li>외부 의존성 패키지가 없거나 표준 라이브러리만 사용되었습니다.</li>
                  )}
                </ul>
              </div>
            </Card>
          </TabPane>
        </Tabs>
      </div>

    </div>
  );
};

export default App;