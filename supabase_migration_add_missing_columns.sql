alter table public.ocr_results
  add column if not exists file_name text,
  add column if not exists file_type text,
  add column if not exists extracted_text text,
  add column if not exists page_num integer,
  add column if not exists pdf_filename text,
  add column if not exists storage_path text,
  add column if not exists storage_url text,
  add column if not exists created_at timestamptz default now();

update public.ocr_results
set pdf_filename = file_name
where pdf_filename is null and file_name is not null;
