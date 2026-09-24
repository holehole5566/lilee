import { Routes } from '@angular/router';
import { Editor } from './editor';
import { Viewer } from './viewer';
import { Configuration } from './configuration';
import { Generator } from './generator';
import { Guide } from './guide';

export const routes: Routes = [
  { path: '', pathMatch: 'full', redirectTo: 'viewer' },
  { path: 'editor', component: Editor },
  { path: 'viewer', component: Viewer },
  { path: 'generator', component: Generator },
  { path: 'guide', component: Guide },
  { path: 'configuration', component: Configuration },
  { path: '**', redirectTo: 'viewer' },
];
