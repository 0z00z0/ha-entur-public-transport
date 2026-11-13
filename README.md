# Entur public transport for Home Assistant

Departures from Entur, set up in the user interface instead of YAML. Stops are found by name, and lines and directions are picked from lists.

Status: 0.6.0 — 2026-11-13 07:15

## Installation

1. In Marketplace, open the menu and choose Custom repositories.
2. Add `https://github.com/0z00z0/ha-entur-public-transport` with the type Integration.
3. Download Entur public transport and restart Home Assistant.
4. Add Entur under Settings, Devices & services.

Updates come through Marketplace. On Home Assistant before 2026.11, HACS works the same way.

The domain is `entur_public_transport`, so this replaces the built-in integration while it is installed. Removing it from `custom_components` brings the built-in one back. Requires Home Assistant 2026.10.0 or newer.

A YAML setup of the built-in sensor is imported on start, one entry per stop place, with platforms joined to their stop. The imported sensors keep the entity ids and names the built-in integration gave them, such as `sensor.entur_nordby_stasjon` and `sensor.entur_nordby_stasjon_platform_1`. A repair notice then asks for the YAML to be removed.

## Setup

1. Search for a stop by name, for example Jernbanetorget.
2. Pick the stop from the matches.
3. Choose lines, platforms, number of departures, whether to hide departures without boarding, whether to add every platform, and whether to show the position on the map.

Each line is listed once per direction, with the destinations shown on the vehicle, for example `31 → Fornebu / Snarøya`. Picking one direction hides departures the other way; picking both, or none, shows everything.

Each stop is one entry. The settings can be changed later under Configure, and the entry reloads on save.

## Sensors

- One sensor for the stop, and one per chosen platform. The state is minutes to the next departure.
- One sensor per chosen line and direction, for example `sensor.nordby_stasjon_r13_dal_eidsvoll` and `sensor.nordby_stasjon_r13_drammen_kongsberg`. A line chosen without a direction gets one sensor per direction it runs. The state and attributes are the same as for the stop sensor, limited to that line and direction.
- The unique id of a line sensor is the stop id, line id and direction key, so a changed destination text does not change the entity.
- A sensor is removed only when its line, direction or platform is deselected, or when adding every platform is turned off. A direction or platform without departures keeps its sensor, which then shows none.
- A line chosen as a whole keeps a sensor for every direction it has had. Choosing some of its directions instead removes the others; directions that have a sensor stay on offer in the form.
- The whole-line choice in the form is labelled in the server language, for example `R13 (both directions)`.
- With adding every platform on, the platforms it added stay until it is turned off.
- On a ring line that shows the same destination in both directions, the less frequent direction can show fewer departures than configured.

## Attributes

The attributes of the built-in YAML sensor keep their names, types and meaning, and are set when there is a departure to fill them:

| Attribute | Value |
|---|---|
| `route`, `route_id` | Next departure: line and destination, `R13 Drammen`, and Entur line id |
| `due_at` | Expected time of the next departure, `HH:MM` |
| `delay` | Delay of the next departure in whole minutes |
| `real_time` | `true` when the next departure has real-time data |
| `next_route`, `next_route_id`, `next_due_at`, `next_delay`, `next_real_time` | The same for the departure after |
| `next_due_in` | Minutes to the departure after, as text, `44 min` |
| `departure_#3` … | Later departures as text, `06:33 R13 Drammen`, prefixed `ca. ` without real-time data |

Every departure slot n from 1 to the configured number of departures is always present:

| Attribute | Value |
|---|---|
| `departure_<n>_route` | Line and destination, `R13 Drammen` |
| `departure_<n>_route_id` | Entur line id, `VYG:Line:R13` |
| `departure_<n>_aimed` | Timetabled departure, local ISO 8601, `2026-11-07T20:03:00+01:00` |
| `departure_<n>_expected` | Expected departure, same format |
| `departure_<n>_delay` | Minutes the expected time is later than the aimed time, truncated toward zero: 59 s is 0, 60–119 s is 1, and up to 59 s early is 0 |
| `departure_<n>_realtime` | `true` when Entur has real-time data for the departure |
| `departures` | All departures as one JSON string, keys `r`, `id`, `a`, `e`, `d`, `rt` matching the rows above |
| `stop_id`, `attribution` | Entur id of the stop or platform, and the data licence |

- A missing value is `null`, never estimated. An empty slot has every field `null` and is left out of `departures`.
- Without real-time data, `realtime` is false and `expected` equals `aimed`.
- `departures` is a string because Home Assistant sends list and dict attributes to ESPHome as Python text, not JSON. Eight departures take about 950 characters.
- The `departure_<n>_…` attributes and `departures` are not recorded in history.

## Disruptions

Each stop also gets a Disruptions sensor, named Avvik in Norwegian. The state is the number of cancelled and cut-short departures. A cut-short departure runs from the stop but is cancelled before the end of its trip.

- `affected_departures` lists them with `line`, `direction`, `destination`, `aimed`, `expected`, `status` and `last_stop`. `status` is `cancelled` or `cut_short`; `last_stop` is the last stop the trip serves, and empty for a cancelled departure. A trip that ends at the stop itself has that stop as `last_stop`.
- `situations` lists Entur's messages for the trips with `summary`, `lines` and `ids`.
- `disruptions` holds the same as one JSON string.
- The unique id is the stop id with `_disruptions`.

The action `entur_public_transport.get_situation` returns the full text of one message. Pass one of the `ids` as `situation_id`; the response has `id`, `summary` and `description`.

```yaml
action: entur_public_transport.get_situation
data:
  situation_id: "NSB:SituationNumber:1692a463-3cc6-42fe-a59d-a5f2a5c2adc6"
response_variable: message
```

## Covered operators

Entur has timetables and real-time data from the authorities and operators below, taken from Entur's own register without duplicates and entries that are not transport providers.

### Authorities (69)

| Logo | Name |
|---|---|
| <img src="docs/logos/agder-kollektivtrafikk-logo.jpg" height="28" alt=""> | Agder Kollektivtrafikk AS |
| <img src="docs/logos/alpinco-logo.png" height="28" alt=""> | Alpinco |
| <img src="docs/logos/askeladden-reiser-logo.png" height="28" alt=""> | Askeladden Reiser |
| <img src="docs/logos/atb-logo.png" height="28" alt=""> | AtB |
| <img src="docs/logos/avinor-logo.png" height="28" alt=""> | Avinor |
| <img src="docs/logos/blindleia-charter-logo.png" height="28" alt=""> | Blindleia Charter |
| <img src="docs/logos/brakar-logo.png" height="28" alt=""> | Brakar |
| <img src="docs/logos/bussring-logo.png" height="28" alt=""> | Bussring |
| <img src="docs/logos/bygdoyfergen-logo.png" height="28" alt=""> | Bygdøyfergen |
| <img src="docs/logos/farte-logo.jpg" height="28" alt=""> | Farte |
| <img src="docs/logos/fjord-line-logo.png" height="28" alt=""> | Fjord Line |
| <img src="docs/logos/fjord-x-logo.png" height="28" alt=""> | Fjord X |
| <img src="docs/logos/fjord1-logo.png" height="28" alt=""> | Fjord1 ASA |
| <img src="docs/logos/flixbus-logo.png" height="28" alt=""> | FlixBus |
| <img src="docs/logos/flybussen-connect-logo.png" height="28" alt=""> | Flybussen Connect |
| <img src="docs/logos/flytoget-logo.png" height="28" alt=""> | Flytoget |
| <img src="docs/logos/flam-travel-logo.png" height="28" alt=""> | Flåm Travel |
| <img src="docs/logos/fram-logo.png" height="28" alt=""> | FRAM |
| <img src="docs/logos/fredrikstad-kommune-logo.png" height="28" alt=""> | Fredrikstad kommune |
| <img src="docs/logos/geiranger-fjordservice-logo.png" height="28" alt=""> | Geiranger Fjordservice |
| <img src="docs/logos/go-ahead-logo.png" height="28" alt=""> | Go-Ahead Norge AS |
| <img src="docs/logos/havila-logo.png" height="28" alt=""> | Havila |
| <img src="docs/logos/hurtigruten-logo.png" height="28" alt=""> | Hurtigruten |
| <img src="docs/logos/hyke-logo.png" height="28" alt=""> | Hyke |
| <img src="docs/logos/innlandstrafikk-logo.png" height="28" alt=""> | Innlandstrafikk |
| <img src="docs/logos/jotunheimen-og-valdresruten-bilselskap-logo.png" height="28" alt=""> | Jotunheimen og Valdresruten Bilselskap |
| <img src="docs/logos/kolumbus-logo.png" height="28" alt=""> | Kolumbus |
| <img src="docs/logos/lustrabaatane-logo.png" height="28" alt=""> | Lustrabaatane |
| <img src="docs/logos/nor-way-logo.png" height="28" alt=""> | NOR-WAY Bussekspress |
| <img src="docs/logos/nordland-fylkeskommune-logo.png" height="28" alt=""> | Nordland fylkeskommune |
| <img src="docs/logos/norled-logo.png" height="28" alt=""> | Norled AS |
| <img src="docs/logos/norrtag-logo.png" height="28" alt=""> | Norrtåg |
| <img src="docs/logos/ruter-logo.png" height="28" alt=""> | Ruter |
| <img src="docs/logos/sj-logo.png" height="28" alt=""> | SJ |
| <img src="docs/logos/sj-nord-logo.png" height="28" alt=""> | SJ Nord |
| <img src="docs/logos/skyss-logo.png" height="28" alt=""> | Skyss |
| <img src="docs/logos/snelandia-logo.png" height="28" alt=""> | Snelandia |
| <img src="docs/logos/snalltaget-logo.png" height="28" alt=""> | Snälltåget |
| <img src="docs/logos/sundbaten-logo.png" height="28" alt=""> | Sundbåten |
| <img src="docs/logos/svipper-logo.png" height="28" alt=""> | Svipper |
| <img src="docs/logos/tide-logo.png" height="28" alt=""> | Tide AS |
| <img src="docs/logos/torghatten-logo.png" height="28" alt=""> | Torghatten |
| <img src="docs/logos/torghatten-sor-logo.png" height="28" alt=""> | Torghatten Sør |
| <img src="docs/logos/ulriken-logo.png" height="28" alt=""> | Ulriken |
| <img src="docs/logos/unibuss-logo.gif" height="28" alt=""> | Unibuss |
| <img src="docs/logos/vestlandske-logo.png" height="28" alt=""> | Vestlandske |
| <img src="docs/logos/vkt-logo.png" height="28" alt=""> | VKT |
| <img src="docs/logos/vy-logo.png" height="28" alt=""> | Vy |
| <img src="docs/logos/vy-bus4you-logo.png" height="28" alt=""> | VY Bus4You |
| <img src="docs/logos/vy-tag-logo.png" height="28" alt=""> | Vy Tåg |
| <img src="docs/logos/vasttrafik-logo.png" height="28" alt=""> | Västtrafik AB |
| <img src="docs/logos/oresundstag-logo.png" height="28" alt=""> | Öresundståg |
| <img src="docs/logos/ostfold-kollektivtrafikk-logo.png" height="28" alt=""> | Østfold kollektivtrafikk |

Also covered: AMBU Pendlerrute, Beffen, Best Arctic Group, Boreal Norge, Fjonesundet, Fjord Tours, Forsvarsbygg Oscarsborgfergen, Go Fjords, Høgsfjordferja, MF Øisang, Norway's best, Rada Reiseliv, The Fjords, Voss Gondol, Vy Buss, Ålesund Turvogn Service.

### Operators (30)

Operators that run services for the authorities above, or on their own account.

| Logo | Name |
|---|---|
| <img src="docs/logos/go-ahead-logo.png" height="28" alt=""> | Go-Ahead Nordic |
| <img src="docs/logos/nobina-logo.png" height="28" alt=""> | Nobina |
| <img src="docs/logos/connect-bus-logo.png" height="28" alt=""> | Connect Bus |
| <img src="docs/logos/sporveien-tbanen-logo.jpg" height="28" alt=""> | Sporveien T-banen |
| <img src="docs/logos/sporveien-trikken-logo.png" height="28" alt=""> | Sporveien Trikken |
| <img src="docs/logos/wideroe-logo.png" height="28" alt=""> | Widerøe |
| <img src="docs/logos/sj-logo.png" height="28" alt=""> | SJ |
| <img src="docs/logos/sj-nord-logo.png" height="28" alt=""> | SJ Nord |
| <img src="docs/logos/tide-logo.png" height="28" alt=""> | Tide |
| <img src="docs/logos/nordlandsbuss-logo.png" height="28" alt=""> | Nordlandsbuss |
| <img src="docs/logos/rodne-trafikk-logo.png" height="28" alt=""> | Rødne Trafikk |
| <img src="docs/logos/firda-billag-logo.png" height="28" alt=""> | Firda Billag |
| <img src="docs/logos/flamsbanen-logo.png" height="28" alt=""> | Flåmsbanen |
| <img src="docs/logos/torp-ekspressen-logo.png" height="28" alt=""> | Torp Ekspressen |
| <img src="docs/logos/flybussen-logo.png" height="28" alt=""> | Flybussen Bergen |
| <img src="docs/logos/flybussen-logo.png" height="28" alt=""> | Flybussen Stavanger |
| <img src="docs/logos/veoy-buss-logo.png" height="28" alt=""> | Veøy Buss |
| <img src="docs/logos/lovundskyss-logo.png" height="28" alt=""> | Lovundskyss |

Also covered: Norwegian, SAS, Boreal, Bastø Fosen, Vidar Hop, Gråkallbanen, Bybanen, Fjord Shuttle, Haukeliekspressen, Hvalerferga, Skilsøferga, Rutebåten Utsira.

Logos are trademarks of their respective owners and are shown only to identify the services covered.

## Licence

Apache-2.0, as Home Assistant core, from which the integration is derived. Data is provided by entur.org under NLOD.
