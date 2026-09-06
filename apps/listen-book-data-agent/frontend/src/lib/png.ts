const INLINE_STYLE_PROPERTIES = [
  'fill',
  'stroke',
  'stroke-width',
  'opacity',
  'font-family',
  'font-size',
  'font-weight',
  'text-anchor',
] as const;

function cloneWithInlineStyles(svg: SVGSVGElement): SVGSVGElement {
  const clone = svg.cloneNode(true) as SVGSVGElement;
  const sourceNodes = [svg, ...Array.from(svg.querySelectorAll<SVGElement>('*'))];
  const cloneNodes = [clone, ...Array.from(clone.querySelectorAll<SVGElement>('*'))];
  sourceNodes.forEach((source, index) => {
    const target = cloneNodes[index];
    if (!target) return;
    const styles = window.getComputedStyle(source);
    INLINE_STYLE_PROPERTIES.forEach((property) => {
      const value = styles.getPropertyValue(property);
      if (value) target.style.setProperty(property, value);
    });
  });
  return clone;
}

export async function exportSvgAsPng(
  svg: SVGSVGElement,
  filename: string,
  background: string,
): Promise<void> {
  const bounds = svg.getBoundingClientRect();
  const width = Math.max(1, Math.round(bounds.width || svg.viewBox.baseVal.width || 800));
  const height = Math.max(1, Math.round(bounds.height || svg.viewBox.baseVal.height || 480));
  const clone = cloneWithInlineStyles(svg);
  clone.setAttribute('width', String(width));
  clone.setAttribute('height', String(height));
  clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg');

  const source = new XMLSerializer().serializeToString(clone);
  const url = URL.createObjectURL(new Blob([source], { type: 'image/svg+xml;charset=utf-8' }));
  try {
    const image = new Image();
    await new Promise<void>((resolve, reject) => {
      image.onload = () => resolve();
      image.onerror = () => reject(new Error('图表图片加载失败'));
      image.src = url;
    });
    const scale = Math.min(2, window.devicePixelRatio || 1);
    const canvas = document.createElement('canvas');
    canvas.width = width * scale;
    canvas.height = height * scale;
    const context = canvas.getContext('2d');
    if (!context) throw new Error('浏览器不支持 PNG 导出');
    context.scale(scale, scale);
    context.fillStyle = background;
    context.fillRect(0, 0, width, height);
    context.drawImage(image, 0, 0, width, height);
    const blob = await new Promise<Blob>((resolve, reject) => {
      canvas.toBlob(
        (value) => (value ? resolve(value) : reject(new Error('PNG 编码失败'))),
        'image/png',
      );
    });
    const downloadUrl = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = downloadUrl;
    anchor.download = filename;
    anchor.click();
    URL.revokeObjectURL(downloadUrl);
  } finally {
    URL.revokeObjectURL(url);
  }
}
