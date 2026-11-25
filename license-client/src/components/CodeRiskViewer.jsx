import React, { useMemo } from 'react';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { vscDarkPlus } from 'react-syntax-highlighter/dist/esm/styles/prism';

const CodeRiskViewer = ({ 
  code, 
  language = 'python', 
  highlightLines = [], 
  selectedLine = null 
}) => {
  const displayCode = code && code.trim().length > 0 
    ? code 
    : `import os\nfrom critical_lib import dangerous_api  # GPL-3.0 독소 조항 충돌 위험\n\ndef auth_call():\n    return dangerous_api()\n\ndef main():\n    print("인증 실행")\n    auth_call()\n\nif __name__ == "__main__":\n    main()`;

  const resolvedLang = useMemo(() => {
    const l = (language || 'python').toLowerCase();
    if (l.includes('py')) return 'python';
    if (l.includes('js') || l.includes('react')) return 'javascript';
    if (l.includes('java')) return 'java';
    if (l.includes('c++') || l.includes('cpp')) return 'cpp';
    if (l.includes('c')) return 'c';
    return 'python';
  }, [language]);

  const autoDangerLines = useMemo(() => {
    if (highlightLines && highlightLines.length > 0) return highlightLines;
    
    const lines = displayCode.split('\n');
    const detected = [];
    lines.forEach((lineText, idx) => {
      const lower = lineText.toLowerCase();
      if (lower.includes('dangerous_api') || lower.includes('gpl') || lower.includes('viral')) {
        detected.push(idx + 1);
      }
    });
    return detected.length > 0 ? detected : [2];
  }, [displayCode, highlightLines]);

  return (
    <div style={{ maxHeight: '240px', overflowY: 'auto', borderRadius: '6px', border: '1px solid #1e293b' }}>
      <SyntaxHighlighter
        language={resolvedLang}
        style={vscDarkPlus}
        showLineNumbers
        wrapLines
        lineProps={(lineNumber) => {
          const isVulnerable = autoDangerLines.includes(lineNumber);
          const isSelected = lineNumber === selectedLine;
          
          let lineStyle = { display: 'block' };
          
          if (isSelected) {
            lineStyle.backgroundColor = 'rgba(114, 46, 209, 0.45)';
            lineStyle.borderLeft = '4px solid #722ed1';
          } else if (isVulnerable) {
            lineStyle.backgroundColor = 'rgba(255, 77, 79, 0.35)';
            lineStyle.borderLeft = '4px solid #ff4d4f';
          }
          return { style: lineStyle };
        }}
        customStyle={{ margin: 0, fontSize: '12.5px', lineHeight: '1.5' }}
      >
        {displayCode}
      </SyntaxHighlighter>
    </div>
  );
};

export default CodeRiskViewer;