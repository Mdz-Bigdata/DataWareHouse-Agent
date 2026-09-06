
const postProcessHtml = (html) => {
  if (!html) return '';
  let res = html;
  
  // The updated regex from MessageRenderer.vue
  res = res.replace(/&lt;a\s+[\s\S]*?href=(?:['"]|&quot;)quick:((?:(?!['"]|&quot;).)+)(?:['"]|&quot;)[\s\S]*?&gt;([\s\S]*?)&lt;\/a&gt;/gi, (match, target, label) => {
    return `<a class="quick-action-btn" href="quick:${target}">${label}</a>`;
  });

  return res;
};

// Test cases
const cases = [
    {
        name: "Standard escaped HTML",
        input: '&lt;a class=&quot;quick-action-btn&quot; href=&quot;quick:%C3%A4%C2%BB%C2%BB%C3%A5%C2%8A%C2%A1&quot;&gt;🙋 Task&lt;/a&gt;',
        expected: '<a class="quick-action-btn" href="quick:%C3%A4%C2%BB%C2%BB%C3%A5%C2%8A%C2%A1">🙋 Task</a>'
    },
    {
        name: "Mixed quotes",
        input: '&lt;a class=\'quick-action-btn\' href="quick:test"&gt;Label&lt;/a&gt;',
        expected: '<a class="quick-action-btn" href="quick:test">Label</a>'
    }
];

let failed = false;
cases.forEach(c => {
    const result = postProcessHtml(c.input);
    if (result !== c.expected) {
        console.error(`❌ Case '${c.name}' failed:\nExpected: ${c.expected}\nActual:   ${result}`);
        failed = true;
    } else {
        console.log(`✅ Case '${c.name}' passed`);
    }
});

if (failed) process.exit(1);
