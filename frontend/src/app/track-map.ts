import { AfterViewInit, Component, ElementRef, EventEmitter, Input, Output, ViewChild } from '@angular/core';
import { Track } from './api';

export interface MapMarker { vehicle: string; node: string; battery: number | null; serviceId: number | null }
interface MapLink { from: string; to: string; both: boolean }

// Presentation coordinates only. Nodes, directed edges and path validity come from /api/track.
const positions: Record<string, [number, number]> = {
  Y: [990, 170], P1A: [760, 65], P1B: [760, 280],
  P2A: [465, 65], P2B: [465, 280], P3A: [90, 65], P3B: [90, 280],
  B1: [875, 65], B2: [885, 280], B3: [660, 65], B4: [665, 155],
  B5: [565, 65], B6: [365, 65], B7: [270, 65], B8: [265, 165],
  B9: [195, 280], B10: [195, 165], B11: [365, 280], B12: [565, 280],
  B13: [665, 205], B14: [660, 280],
};

@Component({
  selector: 'app-track-map', standalone: true,
  styles: [`
    :host { display: block; margin: 1.15rem 0; }
    .map-frame { overflow: hidden; border: 1px solid #e6e5da; border-radius: 18px; background: #faf9f3; box-shadow: 0 10px 26px rgba(41, 67, 75, .06); }
    .map-top { display: flex; justify-content: space-between; align-items: center; gap: 1rem; flex-wrap: wrap; padding: .8rem 1.2rem; border-bottom: 1px solid #e8e8df; background: #fffefa; }
    .map-title { color: #354b57; font-size: .82rem; font-weight: 800; letter-spacing: .12em; text-transform: uppercase; }
    .map-hint { color: #71888c; font-size: .76rem; }
    .map-scroll { overflow-x: auto; }
    svg { display: block; width: 100%; min-width: 850px; height: auto; background: #faf9f3; }
    .station-zone { fill: #fffdf9; stroke: #e8e7dd; stroke-width: 1.5; }
    .station-title { text-anchor: middle; font-size: 16px; font-weight: 800; letter-spacing: .12em; }
    .track-under { stroke: #f4f2ea; stroke-width: 12; stroke-linecap: round; }
    .edge { stroke-width: 5; stroke-linecap: round; opacity: .8; transition: stroke-width .18s, opacity .18s; }
    .edge.active { stroke-width: 9; opacity: 1; filter: drop-shadow(0 2px 3px rgba(32, 54, 68, .18)); }
    .node .shape { fill: #fffefa; stroke: var(--node-color, #4f7782); stroke-width: 3; transition: fill .15s, stroke-width .15s, transform .15s; }
    .node.platform .shape { stroke-width: 4; }
    .node.yard .shape { fill: #344b5f; stroke: #344b5f; }
    .node.yard text { fill: #fff; }
    .node text { font-weight: 800; font-size: 13px; text-anchor: middle; dominant-baseline: central; pointer-events: none; fill: #354a55; }
    .node.block text { font-size: 12px; }
    .node.active .shape { fill: #ffecbb; stroke: #d69032 !important; stroke-width: 5; }
    .node.yard.active .shape { fill: #d69032; }
    .node.available { cursor: pointer; }
    .node.available .shape { stroke-width: 5; }
    .node.available:hover .shape { fill: #e8f6eb; filter: drop-shadow(0 4px 5px rgba(42, 116, 110, .16)); }
    .node.available .halo { fill: none; stroke: #6ab0a0; stroke-width: 1.6; stroke-dasharray: 3 5; opacity: .9; }
    .node:not(.available):not(.active) { opacity: .88; }
    .node.conflict .shape { stroke: #da6558 !important; stroke-width: 5; fill: #fff1eb; }
    .node.conflict .halo { stroke: #da6558; }
    .vehicle { fill: #344b5f; stroke: #fffefa; stroke-width: 2.5; filter: drop-shadow(0 2px 3px rgba(33, 49, 65, .2)); }
    .vehicle-label { font-weight: 800; font-size: 11px; fill: #344b5f; text-anchor: middle; }
    .legend { display: flex; gap: 1rem; align-items: center; flex-wrap: wrap; padding: .65rem 1.1rem; background: #fffefa; border-top: 1px solid #e8e8df; color: #697a80; font-size: .74rem; font-weight: 700; }
    .swatch { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: .28rem; vertical-align: middle; }
    @media (prefers-reduced-motion: reduce) { .edge, .node .shape { transition: none; } }
  `],
  template: `
    <div class="map-frame">
      <div class="map-top"><span class="map-title">Track network</span>
        <span class="map-hint">{{ selectable.length ? 'Tap a dotted station or block to extend the path' : 'Live schematic · directed track' }}</span>
      </div>
      <div class="map-scroll" #mapScroll>
        <svg viewBox="0 0 1080 355" role="img" aria-label="Directed track map; highlighted nodes are selected, dotted nodes can be added to the path">
          <defs>
            <pattern id="paper-dots" width="20" height="20" patternUnits="userSpaceOnUse"><circle cx="2" cy="2" r=".65" fill="#e7e9e0" /></pattern>
            <marker id="map-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse">
              <path d="M 0 1 L 9 5 L 0 9 z" fill="#758793" />
            </marker>
          </defs>
          <rect width="1080" height="355" fill="url(#paper-dots)" />
          @for (station of stations; track station.id) {
            <rect class="station-zone" [attr.x]="station.x" y="29" width="140" height="292" rx="20" />
            <text class="station-title" [attr.x]="station.x + 70" y="183" [attr.fill]="station.color">{{ station.id }}</text>
          }
          @for (link of links; track link.from + '-' + link.to) {
            @if (point(link.from) && point(link.to)) {
              <line class="track-under"
                [attr.x1]="endpoints(link)[0]" [attr.y1]="endpoints(link)[1]"
                [attr.x2]="endpoints(link)[2]" [attr.y2]="endpoints(link)[3]" />
              <line class="edge" [class.active]="edgeSelected(link.from, link.to)"
                [attr.stroke]="edgeSelected(link.from, link.to) ? '#e1963f' : hue(link.from, link.to)"
                [attr.x1]="endpoints(link)[0]" [attr.y1]="endpoints(link)[1]"
                [attr.x2]="endpoints(link)[2]" [attr.y2]="endpoints(link)[3]"
                [attr.marker-start]="link.both ? 'url(#map-arrow)' : null" marker-end="url(#map-arrow)" />
            }
          }
          @for (node of nodes; track node) {
            @if (point(node)) {
              <g class="node" [class.block]="track.nodes[node] === 'BLOCK'"
                 [class.platform]="track.nodes[node] === 'PLATFORM'" [class.yard]="node === 'Y'"
                 [class.active]="path.includes(node)" [class.available]="canSelect(node)"
                 [class.conflict]="conflictNodes.includes(node)" [attr.style]="'--node-color:' + hue(node)"
                 [attr.role]="canSelect(node) ? 'button' : null" [attr.tabindex]="canSelect(node) ? 0 : null"
                 [attr.aria-label]="canSelect(node) ? 'Add ' + node + ' to path' : node"
                 (click)="choose(node)" (keydown.enter)="choose(node)" (keydown.space)="choose(node); $event.preventDefault()">
                @if (canSelect(node)) {
                  <circle class="halo" [attr.cx]="point(node)![0]" [attr.cy]="point(node)![1]" r="33" />
                }
                @if (track.nodes[node] === 'BLOCK') {
                  <circle class="shape" [attr.cx]="point(node)![0]" [attr.cy]="point(node)![1]" r="21" [attr.stroke]="hue(node)" />
                } @else if (node === 'Y') {
                  <rect class="shape" [attr.x]="point(node)![0] - 38" [attr.y]="point(node)![1] - 22" width="76" height="44" rx="16" />
                } @else {
                  <circle class="shape" [attr.cx]="point(node)![0]" [attr.cy]="point(node)![1]" r="29" [attr.stroke]="hue(node)" />
                }
                <text [attr.x]="point(node)![0]" [attr.y]="point(node)![1]">{{ node }}</text>
              </g>
            }
          }
          @for (marker of markers; track marker.vehicle) {
            @if (point(marker.node)) {
              <g [attr.aria-label]="marker.vehicle + ' at ' + marker.node">
                <circle class="vehicle" [attr.cx]="point(marker.node)![0] + markerOffset($index)"
                  [attr.cy]="point(marker.node)![1] + 37" r="10" />
                <text class="vehicle-label" [attr.x]="point(marker.node)![0] + markerOffset($index)"
                  [attr.y]="point(marker.node)![1] + 58">{{ marker.vehicle }}</text>
              </g>
            }
          }
        </svg>
      </div>
      <div class="legend">
        <span><span class="swatch" style="background:#52aaa2"></span> S3</span>
        <span><span class="swatch" style="background:#e4ae56"></span> S2</span>
        <span><span class="swatch" style="background:#e98270"></span> S1</span>
        <span><span class="swatch" style="background:#e1963f"></span> Selected path</span>
        <span><span class="swatch" style="background:#344b5f"></span> Vehicle / Yard</span>
        <span style="margin-left:auto">Arrows show allowed direction · dotted ring = next step</span>
      </div>
    </div>
  `,
})
export class TrackMap implements AfterViewInit {
  @ViewChild('mapScroll') private mapScroll!: ElementRef<HTMLElement>;
  ngAfterViewInit() {
    // The Yard is on the right; start there on narrow screens so path editing is usable.
    if (typeof window !== 'undefined' && window.matchMedia('(max-width: 780px)').matches) {
      this.mapScroll.nativeElement.scrollLeft = this.mapScroll.nativeElement.scrollWidth;
    }
  }
  @Input({ required: true }) track!: Track;
  @Input() path: string[] = [];
  @Input() selectable: string[] = [];
  @Input() markers: MapMarker[] = [];
  @Input() conflictNodes: string[] = [];
  @Output() nodeSelected = new EventEmitter<string>();
  readonly stations = [
    { id: 'S3', x: 20, color: '#3c9890' },
    { id: 'S2', x: 395, color: '#b87d30' },
    { id: 'S1', x: 690, color: '#c46657' },
  ];
  get nodes(): string[] { return Object.keys(this.track.nodes).sort(); }
  get links(): MapLink[] {
    const links = new Map<string, MapLink>();
    for (const [from, to] of this.track.edges) {
      const key = [from, to].sort().join('|');
      const existing = links.get(key);
      if (existing) existing.both = true;
      else links.set(key, { from, to, both: false });
    }
    return [...links.values()];
  }
  point(node: string): [number, number] | undefined { return positions[node]; }
  hue(a: string, b?: string): string {
    const x = (this.point(a)?.[0] ?? 500) + (b ? this.point(b)?.[0] ?? 500 : 0);
    const midpoint = b ? x / 2 : x;
    return midpoint > 710 ? '#e98270' : midpoint > 390 ? '#e4ae56' : '#52aaa2';
  }
  endpoints(link: MapLink): [number, number, number, number] {
    const [x1, y1] = this.point(link.from)!;
    const [x2, y2] = this.point(link.to)!;
    const dx = x2 - x1, dy = y2 - y1;
    const length = Math.hypot(dx, dy) || 1;
    const r1 = link.from === 'Y' ? 40 : this.track.nodes[link.from] === 'BLOCK' ? 23 : 31;
    const r2 = link.to === 'Y' ? 40 : this.track.nodes[link.to] === 'BLOCK' ? 23 : 31;
    return [x1 + dx * r1 / length, y1 + dy * r1 / length,
            x2 - dx * r2 / length, y2 - dy * r2 / length];
  }
  markerOffset(index: number): number { return (index % 5 - 2) * 16; }
  canSelect(node: string): boolean { return this.selectable.includes(node); }
  choose(node: string) { if (this.canSelect(node)) this.nodeSelected.emit(node); }
  edgeSelected(a: string, b: string): boolean {
    return this.path.some((node, index) =>
      (node === a && this.path[index + 1] === b) || (node === b && this.path[index + 1] === a));
  }
}
