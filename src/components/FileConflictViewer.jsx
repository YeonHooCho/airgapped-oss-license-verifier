import React from 'react';
import { Card, Table, Tag, Typography, Alert, Space, Tooltip } from 'antd';
import { 
  SwapOutlined, 
  WarningOutlined, 
  ApiOutlined, 
  CheckCircleOutlined,
  BulbOutlined 
} from '@ant-design/icons';

const { Text } = Typography;

const FileConflictViewer = ({ conflicts = [] }) => {
  const columns = [
    {
      title: '충돌 관계 (호출 모듈 ⟷ 피호출 모듈)',
      key: 'files',
      width: '28%',
      render: (_, record) => (
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '4px 0' }}>
          <div style={{ textAlign: 'left', minWidth: '100px' }}>
            <Tooltip title={`경로: ${record.source_file}`}>
              <Text strong code style={{ fontSize: '13px', color: '#1677ff' }}>
                {record.source_file.split('/').pop()}
              </Text>
            </Tooltip>
            <div style={{ marginTop: 4 }}>
              <Tag color="blue">{record.source_license}</Tag>
            </div>
          </div>

          <SwapOutlined style={{ fontSize: '16px', color: '#ff4d4f', margin: '0 8px' }} />

          <div style={{ textAlign: 'left', minWidth: '100px' }}>
            <Tooltip title={`경로: ${record.target_file}`}>
              <Text strong code style={{ fontSize: '13px', color: '#cf1322' }}>
                {record.target_file.split('/').pop()}
              </Text>
            </Tooltip>
            <div style={{ marginTop: 4 }}>
              <Tag color="volcano">{record.target_license}</Tag>
            </div>
          </div>
        </div>
      ),
    },
    {
      title: '모듈 결합 경로 (Call Trace)',
      dataIndex: 'call_trace',
      key: 'call_trace',
      width: '24%',
      render: (trace) => (
        <Tag 
          icon={<ApiOutlined />} 
          color="default" 
          style={{ 
            fontFamily: 'Consolas, monospace', 
            fontSize: '12px', 
            padding: '4px 8px',
            whiteSpace: 'normal',
            wordBreak: 'break-all',
            background: '#fafafa',
            border: '1px solid #d9d9d9'
          }}
        >
          {trace || 'Direct Module Import'}
        </Tag>
      ),
    },
    {
      title: '법적 비호환 사유 및 권장 조치 가이드',
      key: 'remediation',
      width: '38%',
      render: (_, record) => (
        <div>
          <div style={{ color: '#cf1322', fontWeight: 600, fontSize: '12.5px', marginBottom: 4 }}>
            <WarningOutlined style={{ marginRight: 6 }} />
            {record.conflict_reason}
          </div>
          <div style={{ 
            fontSize: '12px', 
            color: '#389e0d', 
            backgroundColor: '#f6ffed', 
            padding: '6px 10px', 
            borderRadius: 6,
            border: '1px solid #b7eb8f'
          }}>
            <BulbOutlined style={{ marginRight: 6 }} />
            <b>권장 조치:</b> {record.remediation}
          </div>
        </div>
      ),
    },
    {
      title: '심각도',
      dataIndex: 'severity',
      key: 'severity',
      width: '10%',
      align: 'center',
      render: (level) => {
        const isCritical = level === 'Critical';
        return (
          <Tag 
            color={isCritical ? 'red' : 'orange'} 
            style={{ fontWeight: 'bold', padding: '2px 8px' }}
          >
            {level || 'Critical'}
          </Tag>
        );
      },
    },
  ];

  return (
    <Card 
      title={
        <Space>
          <WarningOutlined style={{ color: '#ff4d4f' }} />
          <span style={{ fontWeight: 'bold', fontSize: '15px' }}>
            모듈 간(File-to-File) 상호 라이선스 결합 충돌 분석
          </span>
        </Space>
      }
      bordered={false}
      style={{ marginTop: 16, borderRadius: 8, boxShadow: '0 2px 8px rgba(0,0,0,0.04)' }}
    >
      {conflicts && conflicts.length > 0 ? (
        <>
          <Alert
            message="크로스 모듈(Cross-Module) 라이선스 비호환성 감지"
            description="개별 파일 내부의 선언과 무관하게, 파일 간 모듈 호출 및 바이너리 결합 과정에서 법적 충돌이 발생하는 관계가 식별되었습니다."
            type="error"
            showIcon
            style={{ marginBottom: 16 }}
          />
          <Table
            columns={columns}
            dataSource={conflicts}
            rowKey={(item) => item.id || `${item.source_file}-${item.target_file}`}
            pagination={false}
            size="middle"
            bordered
          />
        </>
      ) : (
        <Alert
          message="파일 간 라이선스 결합 충돌 없음"
          description="모든 모듈 호출 관계(Call Graph)에서 라이선스 양립성(Compatibility)을 만족합니다."
          type="success"
          showIcon
          icon={<CheckCircleOutlined />}
        />
      )}
    </Card>
  );
};

export default FileConflictViewer;