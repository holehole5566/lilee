import { ChangeDetectorRef, Component, Input, OnChanges, OnDestroy, inject } from '@angular/core';
import { Conflict, Schedule, Timeline, Track } from './api';
import { MapMarker, TrackMap } from './track-map';

@Component({
  selector: 'app-playback', standalone: true, imports: [TrackMap],
  template: `
    <section>
      <h2>Track simulation · {{ schedule.scenario }} scenario</h2>
      <p>Derived from this schedule's backend timeline (revision {{ schedule.revision }}). Movement between nodes is instantaneous; a block remains occupied on [arrival, departure). Draft trajectories with unknown position are not animated.</p>
      <app-track-map [track]="track" [markers]="markers()" [conflictNodes]="conflictNodes()" />
      <p><button type="button" (click)="toggle()">{{ playing ? 'Pause' : 'Play' }}</button>
        <label> Speed <select #rate (change)="speed = +rate.value">
          <option value="1">1 simulated second / tick</option>
          <option value="10" selected>10 simulated seconds / tick</option>
          <option value="60">60 simulated seconds / tick</option>
        </select></label>
        <label> Seek <input type="range" min="0" [max]="duration" [value]="offset"
          (input)="seek(+$any($event.target).value)" /></label>
        <strong>{{ at() }}</strong> UTC</p>
      <ul>@for (marker of markers(); track marker.vehicle) {
        <li>{{ marker.vehicle }} · {{ marker.node }} · {{ marker.serviceId === null ? 'Idle' : 'Service #' + marker.serviceId }}
          · Battery {{ marker.battery === null ? 'unknown' : marker.battery.toFixed(2) }}</li>
      }</ul>
      @if (unknownVehicles().length) {
        <p class="error">Position/battery unknown for: {{ unknownVehicles().join(', ') }} (invalid vehicle trajectory).</p>
      }
      @if (activeConflicts().length) {
        <h3>Conflicts at this instant</h3><ul>@for (c of activeConflicts(); track $index) {
          <li class="error">{{ c.code }} · {{ c.resource }} · services {{ c.service_ids.join(', ') }}</li>
        }</ul>
      }
    </section>
  `,
})
export class Playback implements OnChanges, OnDestroy {
  private readonly change = inject(ChangeDetectorRef);
  @Input({ required: true }) schedule!: Schedule;
  @Input({ required: true }) track!: Track;
  offset = 0;
  duration = 0;
  speed = 10;
  playing = false;
  private timer: ReturnType<typeof setInterval> | null = null;

  ngOnChanges() {
    this.stop();
    this.offset = 0;
    const start = Date.parse(this.schedule.scenario_start_at);
    const last = Math.max(start, ...this.schedule.timelines.map(t => Date.parse(t.end_at)));
    this.duration = Math.ceil((last - start) / 1000) + 60;
  }
  ngOnDestroy() { this.stop(); }
  at(): string { return new Date(Date.parse(this.schedule.scenario_start_at) + this.offset * 1000).toISOString(); }
  seek(seconds: number) { this.offset = Math.max(0, Math.min(this.duration, seconds)); this.change.markForCheck(); }
  toggle() {
    if (this.playing) { this.stop(); return; }
    if (this.offset >= this.duration) this.offset = 0;
    this.playing = true;
    this.timer = setInterval(() => {
      this.seek(this.offset + this.speed);
      if (this.offset >= this.duration) this.stop();
    }, 250);
  }
  private stop() {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
    this.playing = false;
    this.change.markForCheck();
  }
  private now(): number { return Date.parse(this.schedule.scenario_start_at) + this.offset * 1000; }
  activeConflicts(): Conflict[] {
    const now = this.now();
    return this.schedule.conflicts.filter(c => c.start && c.end &&
      Date.parse(c.start) <= now && (now < Date.parse(c.end) || c.start === c.end && now === Date.parse(c.start)));
  }
  conflictNodes(): string[] {
    return this.activeConflicts().flatMap(c => this.track.nodes[c.resource ?? ''] ? [c.resource!] :
      this.track.interlockings[c.resource ?? ''] ?? []);
  }
  unknownVehicles(): string[] {
    const now = this.now();
    return Object.entries(this.schedule.unknown_from).filter(([, time]) => now >= Date.parse(time))
      .map(([vehicle]) => vehicle);
  }
  markers(): MapMarker[] {
    const now = this.now();
    const initial = Date.parse(this.schedule.scenario_start_at);
    const vehicles = [...new Set(this.schedule.timelines.map(t => t.service.vehicle_id))].sort();
    return vehicles.flatMap<MapMarker>(vehicle => {
      if (this.schedule.unknown_from[vehicle] && now >= Date.parse(this.schedule.unknown_from[vehicle])) return [];
      const runs = this.schedule.timelines.filter(t => t.service.vehicle_id === vehicle);
      const prior = [...runs].reverse().find(t => Date.parse(t.service.start_at) <= now);
      if (!prior) return [{ vehicle, node: 'Y', serviceId: null,
                            battery: Math.min(100, 80 + (now - initial) / 12000) }];
      if (now >= Date.parse(prior.end_at)) {
        const last = prior.visits[prior.visits.length - 1];
        const charge = last.node === 'Y' && last.battery !== null
          ? Math.min(100, last.battery + (now - Date.parse(prior.end_at)) / 12000) : last.battery;
        return [{ vehicle, node: last.node, serviceId: null, battery: charge }];
      }
      let index = prior.visits.length - 1;
      while (index > 0 && Date.parse(prior.visits[index].arrival) > now) index--;
      const visit = prior.visits[index];
      if (visit.node === 'Y' && now < Date.parse(visit.departure)) {
        const earlier = runs.filter(t => Date.parse(t.end_at) <= Date.parse(prior.service.start_at) && t !== prior).at(-1);
        const arrivalBattery = index > 0 ? prior.visits[index - 1].battery : earlier
          ? Math.min(100, (earlier.visits.at(-1)?.battery ?? 80) +
              (Date.parse(visit.arrival) - Date.parse(earlier.end_at)) / 12000)
          : Math.min(100, 80 + (Date.parse(visit.arrival) - initial) / 12000);
        return [{ vehicle, node: 'Y', serviceId: prior.service.id,
                  battery: arrivalBattery === null ? null : Math.min(100, arrivalBattery + (now - Date.parse(visit.arrival)) / 12000) }];
      }
      return [{ vehicle, node: visit.node, serviceId: prior.service.id, battery: visit.battery }];
    });
  }
}
