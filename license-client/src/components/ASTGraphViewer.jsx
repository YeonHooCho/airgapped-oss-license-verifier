import React, { useEffect, useRef } from 'react';
import * as d3 from 'd3';

const ASTGraphViewer = ({ 
  graphData, 
  analysisResult, 
  selectedNodeId, 
  onSelectNode 
}) => {
  const svgRef = useRef(null);

  const resolveGraphData = () => {
    if (graphData && graphData.nodes && graphData.nodes.length > 0) {
      return graphData;
    }

    const isZip = analysisResult?.input_type === 'zip';
    const mainLic = analysisResult?.ai_guessed_main_license || 'Unknown';
    const isDanger = mainLic.toUpperCase().includes('GPL');

    if (isZip && analysisResult?.results) {
      const nodes = [{ id: 'root_archive', name: '📦 ZIP Project', type: 'root', status: 'safe', line: 1 }];
      const links = [];

      analysisResult.results.slice(0, 8).forEach((item, idx) => {
        const fileId = `file_${idx}`;
        const itemLic = item.ai_guessed_main_license || 'Unknown';
        const fileDanger = itemLic.toUpperCase().includes('GPL');
        
        nodes.push({
          id: fileId,
          name: item.filename || item.file_path || `module_${idx}`,
          type: item.language || 'file',
          status: fileDanger ? 'critical' : 'safe',
          line: 1,
          license: itemLic
        });
        links.push({ source: 'root_archive', target: fileId });
      });

      return { nodes, links };
    }

    const pkgs = Object.keys(analysisResult?.detected_packages || {});
    const targetPkg = pkgs[0] || 'external_dep';

    return {
      nodes: [
        { id: 'main', name: 'main()', type: 'entry', status: 'safe', line: 10 },
        { id: 'auth_call', name: 'auth_call()', type: 'internal', status: 'safe', line: 6 },
        { 
          id: 'danger_api', 
          name: isDanger ? `${targetPkg} (⚠️ ${mainLic})` : `${targetPkg} (${mainLic})`, 
          type: 'external', 
          status: isDanger ? 'critical' : 'safe', 
          line: 2 
        }
      ],
      links: [
        { source: 'main', target: 'auth_call' },
        { source: 'auth_call', target: 'danger_api' }
      ]
    };
  };

  useEffect(() => {
    if (!svgRef.current) return;

    const data = resolveGraphData();
    const width = svgRef.current.clientWidth || 560;
    const height = 240;

    d3.select(svgRef.current).selectAll('*').remove();

    const svg = d3.select(svgRef.current)
      .attr('viewBox', [0, 0, width, height]);

    const g = svg.append('g');

    const zoom = d3.zoom()
      .scaleExtent([0.5, 3])
      .on('zoom', (event) => g.attr('transform', event.transform));
    svg.call(zoom);

    svg.append('defs').append('marker')
      .attr('id', 'ast-arrow')
      .attr('viewBox', '0 -5 10 10')
      .attr('refX', 22)
      .attr('refY', 0)
      .attr('markerWidth', 6)
      .attr('markerHeight', 6)
      .attr('orient', 'auto')
      .append('path')
      .attr('d', 'M0,-5L10,0L0,5')
      .attr('fill', '#94a3b8');

    const nodes = data.nodes.map(d => ({ ...d }));
    const links = data.links.map(d => ({ ...d }));

    const simulation = d3.forceSimulation(nodes)
      .force('link', d3.forceLink(links).id(d => d.id).distance(85))
      .force('charge', d3.forceManyBody().strength(-200))
      .force('center', d3.forceCenter(width / 2, height / 2));

    const link = g.append('g')
      .attr('stroke', '#cbd5e1')
      .attr('stroke-width', 2)
      .selectAll('line')
      .data(links)
      .join('line')
      .attr('marker-end', 'url(#ast-arrow)');

    const drag = (sim) => {
      return d3.drag()
        .on('start', (event, d) => {
          if (!event.active) sim.alphaTarget(0.3).restart();
          d.fx = d.x;
          d.fy = d.y;
        })
        .on('drag', (event, d) => {
          d.fx = event.x;
          d.fy = event.y;
        })
        .on('end', (event, d) => {
          if (!event.active) sim.alphaTarget(0);
          d.fx = null;
          d.fy = null;
        });
    };

    const node = g.append('g')
      .selectAll('g')
      .data(nodes)
      .join('g')
      .call(drag(simulation))
      .on('click', (event, d) => {
        if (onSelectNode) onSelectNode(d);
      })
      .style('cursor', 'pointer');

    node.append('circle')
      .attr('r', 16)
      .attr('fill', d => d.status === 'critical' ? '#fff1f0' : '#e6f4ff')
      .attr('stroke', d => {
        if (d.id === selectedNodeId) return '#722ed1';
        return d.status === 'critical' ? '#ff4d4f' : '#1677ff';
      })
      .attr('stroke-width', d => (d.id === selectedNodeId || d.status === 'critical') ? 3 : 1.5);

    node.append('text')
      .text(d => d.name)
      .attr('x', 0)
      .attr('y', 25)
      .attr('text-anchor', 'middle')
      .attr('font-size', '11px')
      .attr('fill', '#1e293b')
      .attr('font-weight', d => d.status === 'critical' ? 'bold' : 'normal');

    simulation.on('tick', () => {
      link
        .attr('x1', d => d.source.x)
        .attr('y1', d => d.source.y)
        .attr('x2', d => d.target.x)
        .attr('y2', d => d.target.y);

      node.attr('transform', d => `translate(${d.x},${d.y})`);
    });

    return () => simulation.stop();
  }, [graphData, analysisResult, selectedNodeId]);

  return (
    <div style={{ width: '100%', height: '230px', background: '#fafafa', borderRadius: '8px', border: '1px solid #f0f0f0', overflow: 'hidden' }}>
      <svg ref={svgRef} style={{ width: '100%', height: '100%' }} />
    </div>
  );
};

export default ASTGraphViewer;