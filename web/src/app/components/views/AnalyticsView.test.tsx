import { render } from '@testing-library/react';
import { Bar, BarChart, Pie, PieChart, XAxis, YAxis } from 'recharts';
import { describe, expect, it } from 'vitest';

import { DataColoredBar, DataColoredSector } from './AnalyticsView';

describe('AnalyticsView chart shapes', () => {
  it('preserves per-contact colors without the deprecated Cell component', () => {
    const { container } = render(
      <PieChart width={200} height={200}>
        <Pie
          data={[{ name: 'famille', value: 3, color: '#123456' }]}
          dataKey="value"
          cx={100}
          cy={100}
          innerRadius={40}
          outerRadius={80}
          isAnimationActive={false}
          shape={DataColoredSector}
        />
      </PieChart>,
    );

    expect(container.querySelector('path[fill="#123456"]')).not.toBeNull();
  });

  it('preserves per-task colors without the deprecated Cell component', () => {
    const { container } = render(
      <BarChart width={240} height={180} data={[{ name: 'Terminées', value: 4, fill: '#654321' }]}>
        <XAxis dataKey="name" />
        <YAxis />
        <Bar dataKey="value" isAnimationActive={false} shape={DataColoredBar} />
      </BarChart>,
    );

    expect(container.querySelector('path[fill="#654321"]')).not.toBeNull();
  });
});
